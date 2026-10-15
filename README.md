# PriceQuery

Ask questions about scraped price data in plain English. A LangChain prompt gives an LLM the database
schema and a few worked examples and gets a SQL query back. Every query is checked and run on a read-only
connection with a row cap, and if the database rejects it the error goes back to the model once for a fix.

Stack: Python, Flask (REST API), LangChain (`langchain-core` prompts and runnables, `langchain-openai` for the model),
SQLAlchemy, MySQL (SQLite for the demo and the default tests).

It reads the four tables written by [ecommerce-price-tracker](https://github.com/Aravindashok18/ecommerce-price-tracker)
(`categories`, `products`, `price_history`, `price_alerts`), so it can be pointed at that database.

```
question
   |
   v
nl2sql.py     prompt = live schema (from the database) + rules + worked examples + today's date
   |          model replies with SQL, or NO_QUERY if the schema can't answer
   v
guard.py      one statement only, must be a SELECT, no INTO/locking/sleep/file functions,
   |          blocked columns (email) rejected, LIMIT added or lowered      -> UnsafeSQL, never retried
   v
executor.py   read-only connection, query timeout, reads at most max_rows + 1 rows
   |                                                                        -> QueryError
   |          database error? send the SQL + error back to the model, ONE retry
   v
rows (+ a flag if they were cut off)
```

## Safety layers

Generated SQL is untrusted input, so three independent things stand between it and the data:

1. **The database user.** `db/readonly_user.sql` creates a user that can only `SELECT`, with a column-level
   grant that leaves `price_alerts.email` unreadable. This is the layer that doesn't depend on this code
   being right. Point `DATABASE_URL` at this user.
2. **The connection.** SQLite is opened `mode=ro` with `PRAGMA query_only`; MySQL sessions are set
   `READ ONLY` with a `MAX_EXECUTION_TIME`. Writes fail even if a statement gets past the guard
   (tested: `DELETE`/`DROP`/`UPDATE`/`INSERT` sent straight to the executor are refused by the database).
3. **The guard.** A single read-only `SELECT` or nothing: it parses the SQL, so keywords inside string
   literals (`WHERE title = 'drop table'`) are fine while `;`-chained statements, `INTO OUTFILE`,
   `FOR UPDATE`, `SLEEP()`, `/*! ... */` comments and quoted identifiers like `"email"` are not.

The row cap asks the database for `max_rows + 1` rows, so when more existed the result is flagged as
cut off rather than silently showing exactly `max_rows`.

Retry policy: a database error (bad column, bad syntax) gets one corrected attempt. Statements the guard
refuses are not retried, and neither are timeouts, since a rewrite would not help and the refusal is the
point.

## REST API

`pricequery/api.py` puts the same pipeline behind HTTP. The API only turns outcomes into status codes: the model, the guard and the read-only connection are the ones the CLI uses.

```bash
python -m pricequery.api                     # http://127.0.0.1:5060 (or: gunicorn "pricequery.api:create_app()")
curl -s localhost:5060/api/ask -H 'Content-Type: application/json' \
     -d '{"question": "How many products are in each category?"}'
```

| Route | Description |
|---|---|
| `POST /api/ask` | `{"question"}` returns `{question, sql, columns, rows, row_count, truncated, attempts}`. `sql` is the statement that actually ran, after the guard capped it. |
| `GET /api/schema` | The schema text the model is shown, with blocked columns left out. |
| `GET /health` | `{status, dialect, model, max_rows}` |

| Status | `error` | When |
|---|---|---|
| 400 | `bad_request` | not JSON, no `question`, empty, or over 500 characters. The model is never called. |
| 422 | `unsafe_sql` | the guard refused the generated statement. Nothing ran, and it is not sent back to the model. |
| 422 | `cannot_answer` | the model replied `NO_QUERY`: the schema cannot answer. |
| 502 | `query_failed` | the database rejected the query, and the one corrected retry failed too. |
| 504 | `timeout` | the query hit the time limit and was stopped. |
| 503 | `model_unavailable` | the model call failed. The error text is not passed on to the client. |

MySQL's `DECIMAL` and datetime values are sent as numbers and ISO 8601 strings. There is no authentication or rate limiting: put it behind something that has them, because every request costs a model call.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pricequery.seed                    # creates data/demo.db (synthetic, see below)

# run a SELECT through the guard + read-only connection, no model needed
python -m pricequery.cli --sql "SELECT c.name, COUNT(*) FROM products p JOIN categories c ON c.id = p.category_id GROUP BY c.name"
python -m pricequery.cli --sql "DROP TABLE products"      # -> refused: only SELECT statements are allowed (got DROP)

# plain-English questions need a model
pip install -r requirements-llm.txt
export OPENAI_API_KEY=...
# or a local model through Ollama's OpenAI-compatible endpoint, no key needed:
#   OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.1:8b
python -m pricequery.cli --show-sql "Which category has the highest average price right now?"
```

For MySQL: create the tables with `db/schema.sql`, create the read-only user with `db/readonly_user.sql`,
and set `DATABASE_URL=mysql+pymysql://pricequery_ro:<password>@localhost/pricedb`. MySQL 8 and later log in with `caching_sha2_password`, which PyMySQL can only do with the `cryptography` package (it is in `requirements.txt`). Each process keeps at most `DB_POOL_SIZE` (default 5) connections open, and the read-only user is allowed 20, so keep processes x pool size under that.
`python -m pricequery.ddl` regenerates `db/schema.sql` from the models.

The demo database is **synthetic**: 60 products in 6 categories with 90 days of daily prices, a few
stock changes and 15 alerts, generated from a fixed seed by `pricequery/seed.py`. It is not scraped data.

## Checking answers against hand-written SQL

`eval/questions.json` has 21 questions with a hand-written SQL answer and 3 the schema cannot answer.
`python -m eval.run_eval` asks the model each question, runs its query and the hand-written one on the
same database, and compares the two result sets strictly: same rows and values, floats to 2 decimals, row
order only when the question asks for ordering, and an extra column counts as a miss. The three
unanswerable questions pass only if the model replies `NO_QUERY`. Results go to `eval/results.csv`.

`python -m eval.run_eval --gold-only` needs no model: it confirms all 21 hand-written queries pass the
guard, run, and return rows.

## Result with a real model

`python -m eval.run_eval` was run once against **llama3.1:8b**, served locally by Ollama through its OpenAI-compatible endpoint (temperature 0, one run). It is not GPT-4o-mini or any hosted model, and none of those were run. The raw output is [`eval/results_llama3.1-8b.csv`](eval/results_llama3.1-8b.csv).

- **Answerable questions: 15 of 21 match the hand-written SQL** under the strict comparison above. With 21 questions, the 95% interval for that rate is roughly 50% to 86%. No question needed the retry.
- **Unanswerable questions: 1 of 3 correctly declined** (`NO_QUERY`); the other two got a query that ran.
- The six misses: in three (q04, q05, q14) the values were right but the query returned an extra column, which the strict comparison counts as a miss. The other three were wrong: q09 left out `DISTINCT` and returned duplicate titles; q18 averaged over every price in history instead of the current price; q21 compared a datetime column to a bare date and counted 0.

## What was verified

**134 tests**: 112 run anywhere in a few seconds on SQLite, and 22 more (`tests/test_mysql.py`) run only when `PRICEQUERY_MYSQL_ADMIN_URL` points at a MySQL server they may create a scratch database and user on.

- **Guard**: 22 unsafe statements rejected (chained statements, DML/DDL, `INTO OUTFILE`, `SLEEP`, locking reads, hidden `/*!` comments, `PRAGMA`/`ATTACH`, quoted blocked columns, ...) and 7 tricky-but-valid ones accepted; row-limit rewriting for `LIMIT n`, `LIMIT n OFFSET m`, `LIMIT m, n` and subqueries.
- **Executor**: writes refused by the database itself, with the row count unchanged afterwards; row cap and truncation flag; a runaway recursive query stopped by the timeout; `:` and `%` inside literals; the pool bounded on server databases.
- **Engine**, with a scripted stand-in for the model: SQL extracted from fences; the prompt contains the schema, examples, date and dialect and not the blocked column; one retry on a SQL error with the failed SQL and the database message in the retry prompt; a second failure is raised, not retried again; unsafe SQL refused with a single model call and the table intact; `NO_QUERY` handled.
- **REST API** (28 tests): each outcome in the table above, malformed requests never reaching the model, destructive statements refused with the data untouched, a model outage not leaking its error text, row-cap truncation, JSON types.
- **Eval harness**: the comparator, plus a model that returns the hand-written SQL scores 21/21 and a plausible-but-wrong query is marked wrong. All 21 hand-written queries run and return rows on SQLite and on MySQL.
- **MySQL**, against a real server (MySQL 9.7.2, a scratch instance): the shipped `db/schema.sql` and `db/readonly_user.sql` applied as written; the seeded data loaded into that schema; writes refused twice over (by the user's grants, and, for an account that does have write rights, by the `READ ONLY` session); the column-level grant hiding `price_alerts.email` (`SELECT email` and `SELECT *` both denied); a runaway query stopped by `MAX_EXECUTION_TIME` and not retried; a MySQL error message reaching the retry prompt; the REST API returning MySQL rows as JSON.

Running on a real server found two bugs that SQLite could not show, both fixed:

1. **PriceQuery could not start with the read-only user this repo ships.** SQLAlchemy describes MySQL tables with `SHOW CREATE TABLE`, which MySQL refuses to a user who only has column-level grants (the grant that hides `email`). The schema is now read from `information_schema` on MySQL, which lists exactly the columns the user may see.
2. **The connection pool could exceed the user's connection limit.** SQLAlchemy's default pool opens up to 15 connections; the read-only user was limited to 5, so concurrent requests would fail with `max_user_connections`. Pools are now capped (`DB_POOL_SIZE`) and the user's limit raised to 20.

## What was not verified

- **Hosted models.** Accuracy was measured for one local 8B model, once, on 21 questions. Nothing was run against GPT-4o-mini or any other hosted model, so the repo has no figure for them.
- **MySQL 8.4 and managed MySQL.** The server used was MySQL 9.7.2 (a conda-forge build run locally). RDS, Aurora and the 8.4 LTS release were not tried.
- The timeout is verified on SQLite and MySQL with a runaway query. A valid but expensive `SELECT` is bounded by the timeout and the row cap, not prevented.
- Only prices in the demo schema are described to the model (GBP); another schema needs its own column notes in `pricequery/schema.py`.
- The API has no authentication, rate limiting or CORS, and was not load-tested.
- Nothing here measures any effect on a real reporting workload: the demo database is synthetic.
