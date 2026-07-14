from flask import Flask, request, render_template, redirect, url_for
import os
import sqlite3
import uuid
from dotenv import load_dotenv

app = Flask(__name__)

# --- Database backend selection ---------------------------------------
# Reads a local .env file (if present) into the environment. This lets you
# keep secrets (COSMOS_KEY, PG_PASSWORD, ...) out of your shell history /
# .bashrc - see .env.example. In production you'd typically set these as
# real environment variables (e.g. via your host/App Service config)
# instead of a .env file; load_dotenv() won't override variables that are
# already set that way.
load_dotenv()

# Three backends are supported. Which one is used is decided automatically
# from whichever environment variables are set - no code changes needed to
# switch. Precedence, first match wins:
#
#   1. Azure Cosmos DB               - if COSMOS_ENDPOINT is set
#   2. Azure Database for PostgreSQL - else if PG_HOST is set
#   3. SQLite (built-in)              - otherwise, no configuration needed
#
# This keeps the app runnable out of the box (SQLite) while letting you
# point it at either managed database service just by setting env vars.

COSMOS_ENDPOINT = os.environ.get('COSMOS_ENDPOINT')
COSMOS_KEY = os.environ.get('COSMOS_KEY')
COSMOS_DB = os.environ.get('COSMOS_DB', 'ICC1db')
COSMOS_CONTAINER = os.environ.get('COSMOS_CONTAINER', 'tasks')

PG_HOST = os.environ.get('PG_HOST')
PG_PORT = os.environ.get('PG_PORT', '5432')
PG_DB = os.environ.get('PG_DB', 'icc1db')
PG_USER = os.environ.get('PG_USER')
PG_PASSWORD = os.environ.get('PG_PASSWORD')
# Azure Database for PostgreSQL requires SSL; 'require' works for both the
# Flexible Server and single-server deployment types.
PG_SSLMODE = os.environ.get('PG_SSLMODE', 'require')

if COSMOS_ENDPOINT:
    BACKEND = 'cosmos'
elif PG_HOST:
    BACKEND = 'postgres'
else:
    BACKEND = 'sqlite'


if BACKEND == 'cosmos':
    from azure.cosmos import CosmosClient, PartitionKey

    client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
    database = client.create_database_if_not_exists(id=COSMOS_DB)
    container = database.create_container_if_not_exists(
        id=COSMOS_CONTAINER,
        partition_key=PartitionKey(path="/id")
    )

elif BACKEND == 'postgres':
    import psycopg2

    def get_pg_conn():
        return psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
            sslmode=PG_SSLMODE
        )

    def init_pg():
        conn = get_pg_conn()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                task TEXT NOT NULL,
                priority INTEGER DEFAULT 1
            )
        ''')
        conn.commit()
        cur.close()
        conn.close()

    init_pg()

else:
    def init_db():
        conn = sqlite3.connect('todo.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY,
                task TEXT NOT NULL,
                priority INTEGER DEFAULT 1
            )
        ''')
        conn.commit()
        conn.close()

    init_db()


# --- Data access layer --------------------------------------------------
# All three backends are normalised to the same shape - a list of dicts
# with 'id', 'task' and 'priority' keys - so the routes and templates
# below don't need to know or care which database is actually in use.

def get_all_tasks():
    if BACKEND == 'cosmos':
        tasks = list(container.read_all_items())
        tasks.sort(key=lambda t: t.get('priority', 1))
        return tasks

    if BACKEND == 'postgres':
        conn = get_pg_conn()
        cur = conn.cursor()
        cur.execute('SELECT id, task, priority FROM tasks ORDER BY priority ASC')
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{'id': row[0], 'task': row[1], 'priority': row[2]} for row in rows]

    conn = sqlite3.connect('todo.db')
    cur = conn.cursor()
    cur.execute('SELECT id, task, priority FROM tasks ORDER BY priority ASC')
    rows = cur.fetchall()
    conn.close()
    return [{'id': row[0], 'task': row[1], 'priority': row[2]} for row in rows]


def create_task(task, priority):
    if BACKEND == 'cosmos':
        task_doc = {
            'id': str(uuid.uuid4()),
            'task': task,
            'priority': priority
        }
        container.upsert_item(task_doc)
        return

    if BACKEND == 'postgres':
        conn = get_pg_conn()
        cur = conn.cursor()
        cur.execute('INSERT INTO tasks (task, priority) VALUES (%s, %s)', (task, priority))
        conn.commit()
        cur.close()
        conn.close()
        return

    conn = sqlite3.connect('todo.db')
    cur = conn.cursor()
    cur.execute('INSERT INTO tasks (task, priority) VALUES (?, ?)', (task, priority))
    conn.commit()
    conn.close()


def remove_task(task_id):
    if BACKEND == 'cosmos':
        container.delete_item(item=task_id, partition_key=task_id)
        return

    if BACKEND == 'postgres':
        conn = get_pg_conn()
        cur = conn.cursor()
        # Postgres's id column is an integer (SERIAL); task_id arrives from
        # the URL as a string, so it must be cast before binding, otherwise
        # Postgres raises "operator does not exist: integer = text".
        cur.execute('DELETE FROM tasks WHERE id = %s', (int(task_id),))
        conn.commit()
        cur.close()
        conn.close()
        return

    conn = sqlite3.connect('todo.db')
    cur = conn.cursor()
    cur.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()


# --- Routes ---------------------------------------------------------------

# Route for the home page
@app.route('/')
def home():
    return render_template('index.html')  # Render a home page with a link to the task manager


# Route for the task manager page
@app.route('/tasks')
def tasks():
    return render_template('tasks.html', tasks=get_all_tasks())


# Route to add a new task
@app.route('/add', methods=['POST'])
def add_task():
    new_task = request.form.get('task')
    priority = int(request.form.get('priority', 1))
    create_task(new_task, priority)
    return redirect(url_for('tasks'))


# Route to delete a task
@app.route('/delete/<task_id>', methods=['POST'])
def delete_task(task_id):
    remove_task(task_id)
    return redirect(url_for('tasks'))


if __name__ == '__main__':
    app.run(debug=False,
    host='0.0.0.0',
    port=8080
    )
