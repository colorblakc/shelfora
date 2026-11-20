<p align="center">
  <img src="docs/readme-banner.svg" alt="Shelfora — a better place for every book. Your library, all in one place." width="100%">
</p>

<h1 align="center">Shelfora</h1>

<p align="center">
  <img alt="Python 3.10 or newer" src="https://img.shields.io/badge/Python-3.10%2B-3c38cc?style=for-the-badge&amp;logo=python&amp;logoColor=white">
  <img alt="Flask 3.1" src="https://img.shields.io/badge/Flask-3.1-222655?style=for-the-badge&amp;logo=flask&amp;logoColor=white">
  <img alt="MySQL" src="https://img.shields.io/badge/MySQL-database-3166a1?style=for-the-badge&amp;logo=mysql&amp;logoColor=white">
  <img alt="Single library management" src="https://img.shields.io/badge/Library-management-ee6268?style=for-the-badge&amp;logoColor=white">
</p>

<p align="center">
  <strong>A thoughtful home for your books, your readers and the everyday work of running a library.</strong><br>
  One place for the catalogue, circulation, reading room and library desk.
</p>

<p align="center">
  <a href="#features">Features</a> &nbsp;·&nbsp;
  <a href="#local-setup">Local setup</a> &nbsp;·&nbsp;
  <a href="#deployment">Deployment</a> &nbsp;·&nbsp;
  <a href="#configuration">Configuration</a>
</p>

---

Shelfora is a single-library management application built with Flask, MySQL, HTML, CSS and JavaScript. Readers can browse the catalogue, reserve books, book reading-room seats and manage their accounts. Librarians manage the collection, circulation, members and fines from the library desk.

## Features

| The collection | The library desk | The reading room |
| --- | --- | --- |
| Search titles, place holds and follow every loan. | Track copies, members, returns and fines. | Book seats and manage daily sessions. |

### For readers

- Browse and search the starter catalogue by title, author, subject, ISBN, publisher or description. Book pages show availability and the bibliographic details recorded for that edition.
- Create a member account, update contact details and choose an avatar. Change your password in **Profile → Password** using your current password. There is no password-reset-by-email flow.
- Request an available book, join a waitlist when all copies are unavailable, cancel a request and see its status on **My shelf**. An approved hold lasts three days; the next waitlisted reader can be offered a released copy.
- See current loans, due dates, borrowing history, fines and seat bookings. Request a renewal from My shelf; a librarian reviews it. An approved renewal adds 14 days when the book is not overdue and no other reader is waiting.
- Book or release a reading-room seat. The starter room has 100 seats and morning (9 am–1 pm) and afternoon (1–5 pm) sessions in India Standard Time. Bookings are available up to 14 days ahead.
- See in-app notices for due, overdue and ready-to-collect books.
- Ask Mira, the read-only AI Assistant, about the catalogue, library services and their own account. The assistant requires a separate API key and sends the relevant question and account context to the configured provider.

### For librarians

- Open **Library desk** at `/desk` with an administrator account. Manage catalogue records, member details, reservations, loans, returns and fines. Removing a member or title preserves its circulation history.
- Track each physical copy with a copy ID. Staff can enter or scan its ID when issuing or returning; an issue without an entered ID selects an available copy. Record a return as good, damaged or lost, and restore a damaged or lost copy from its book record when appropriate.
- Configure the borrowing limit under **Library desk → Loans**. The default is three active loans. An overdue loan, unpaid fine or reached limit blocks a new issue; staff can record a reason to override the restriction. Closing an account also checks unpaid fines, with a recorded override available.
- Record manual fines and edit or waive eligible fines before a payment attempt exists. Overdue fines use the rate saved when the loan was issued and are finalized on return. The starter rate is ₹2 per overdue calendar day; the default due date is 14 days after issue.
- Accept configured online fine payments, show printable receipts and submit full refunds for confirmed payments. The application verifies payments server-side and reuses a stable idempotency key for a refund retry. Refund status and payment-order exceptions may require provider review; webhook-based automatic updates are not implemented.

Starter titles and copy counts are sample data, not a guarantee that physical copies exist. ISBN and publisher fields can be empty when an edition is unknown. Existing active loans upgraded from older versions receive **provisional** copy assignments, which staff should confirm on return; older returned loans remain unassigned.

---

## Requirements

- Python 3.10 or later and `pip`.
- A running MySQL server, an empty `utf8mb4` database and a database user with permission to create and modify tables in it.
- Optional: an AI provider key for Mira and payment keys for fine checkout.

The Python dependencies are pinned in `requirements.txt`. MySQL is required; the application does not currently support SQLite.

## Local setup

1. Create a MySQL database named `shelfora` and a database user with access to it. Use your preferred MySQL administration tool; for example, as a MySQL administrator:

   ```sql
   CREATE DATABASE shelfora CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'shelfora'@'localhost' IDENTIFIED BY 'replace-with-a-strong-password';
   GRANT ALL PRIVILEGES ON shelfora.* TO 'shelfora'@'localhost';
   ```

   If the user already exists, grant that user access instead. Adapt the host and credentials to your MySQL installation.

2. From the project directory, create a virtual environment and install dependencies:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   mkdir -p instance
   cp config.example.py instance/config.py
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

   On Windows, create the environment with `py -m venv .venv`, activate it with `.venv\Scripts\activate`, and copy `config.example.py` to `instance/config.py`.

3. Edit `instance/config.py`. Set `SECRET_KEY` to the generated value and fill in `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD` and `MYSQL_DATABASE`. For an installation using a Unix socket, add `MYSQL_SOCKET = "/path/to/mysql.sock"`. For **local HTTP only**, set `SESSION_COOKIE_SECURE = False`.

4. Initialize the database, create an administrator and start the development server:

   ```sh
   flask --app app init-db
   flask --app app create-admin
   flask --app app run --port 5082
   ```

   `create-admin` prompts for a name, email and password. Sign in with that account and visit [http://127.0.0.1:5082/desk](http://127.0.0.1:5082/desk). Public registration creates member accounts, not administrators.

5. Optionally add labelled demonstration members and activity:

   ```sh
   flask --app app seed-demo
   ```

   Demo members receive random non-login passwords. Re-running the command leaves existing demo records unchanged. Use sample data only in a development database.

`init-db` creates missing tables and columns and adds starter catalogue records without resetting existing circulation records. **Back up an existing database first**, especially before upgrading copy records. For information on the app's environment and configuration options, see [Configuration](#configuration).

## Configuration

`instance/config.py` is for Flask and database settings. Flask also accepts configuration environment variables prefixed with `SHELFORA_`, such as `SHELFORA_MYSQL_DATABASE`. A hosted database that supplies plain `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD` and `MYSQL_DATABASE` variables is read automatically and connected over TLS. For the assistant and payment integration, use server environment variables or a private project-root `.env` file:

```dotenv
GROQ_API="your-assistant-api-key"
RZP_KEY_ID="your-payment-key-id"
RZP_KEY_SECRET="your-payment-key-secret"
```

Do not commit `instance/config.py`, `.env`, credentials or database backups. Use payment **test keys** during development and verify the complete checkout and refund flows in the provider's test environment before enabling live keys. The assistant and payments are optional; without their keys, the corresponding service is unavailable.


---

## Deployment

> **Why a VPS and not a paid platform?** This is a mini-project with a limited scope, so we host it on a VPS-style container host (Altare Docker Apps and Altare managed MySQL, free plan) that pulls a Docker image built from this public GitHub repository. In a real-world project we would weigh the options properly and host it on a paid plan of a managed platform, with managed databases, backups, uptime guarantees and a custom domain.

Shelfora ships as a Docker image. Every push to `main` builds the image on GitHub and publishes it to GitHub's container registry. The host pulls and runs that image and connects it to a separate MySQL database.

```
git push  ->  GitHub Actions builds the image  ->  ghcr.io/OWNER/REPO:latest  ->  host pulls and runs it  ->  MySQL
```

### 1. Put the code on a public GitHub repository

Create a public repository and push the project to the `main` branch. The workflow in `.github/workflows/docker.yml` runs by itself and publishes the image as `ghcr.io/<owner>/<repo>:latest` (the name is always lower case).

**The repository is public, so it must never contain secrets.** `.env` and `instance/` are listed in `.gitignore`; keep it that way. All keys are entered later as environment variables on the host. If a key was ever committed, treat it as leaked and create a new one.

### 2. Make the image public

On GitHub open your profile, **Packages**, choose the image, then **Package settings**, and set **Change visibility** to **Public** (new packages can start as private). The host can then pull it without a login.

### 3. Create the MySQL database

In the Altare console open **Storage**, **MySQL**, and choose **Create database**. Pick a region and the smallest size the plan allows. Altare provides MySQL 8.4 over an encrypted connection.

### 4. Create the app and connect the database

1. In **Apps** choose **New app**, then **Docker image**. Set **Image** to `ghcr.io/<owner>/<repo>:latest` and **Port** to `8000`. No data folder is needed because all data lives in MySQL.
2. Add these variables (store keys and passwords as **Secrets** if the console offers them):

| Variable | Value |
|---|---|
| `SHELFORA_SECRET_KEY` | a long random secret: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `SHELFORA_ADMIN_NAME`, `SHELFORA_ADMIN_EMAIL`, `SHELFORA_ADMIN_PASSWORD` | the first librarian account, created on start if missing (password of 10 to 128 characters) |
| `GROQ_API` | optional; key for the Mira assistant |
| `RZP_KEY_ID`, `RZP_KEY_SECRET` | optional; payment **test** keys |

3. Open the database's **Overview** and use **Connect to an app** to attach it to the app. Altare adds `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD` and `MYSQL_DATABASE` and restarts the app. Shelfora reads these variables and turns on the encrypted connection by itself.

The console labels may differ slightly from the ones above. Altare gives the app an HTTPS address such as `https://your-app.go.altare.global`.

### 5. What happens on every start

`docker-entrypoint.sh` runs these steps, then starts the web server:

1. `flask init-db` creates or upgrades the tables and adds the starter catalogue. It keeps existing records, and the script retries for about a minute if the database is still starting.
2. `flask ensure-admin` creates the librarian account from the `SHELFORA_ADMIN_*` variables if it does not exist.
3. `gunicorn` serves the app on port 8000.

### 6. Check that it works

- Open the address, browse the catalogue, create a member account and reserve a book.
- Sign in as the librarian at `/desk`, approve the request, issue the book and record its return.
- Book a reading-room seat.
- If Mira or payments are configured, ask Mira a question and check that the payment service is reachable.

### Updating, and starting again

- **Update:** push to `main`, wait for the **Actions** tab to show the image built, then redeploy the app on Altare (or turn on its automatic image check, which looks for a new image every 15 minutes). `init-db` upgrades the tables on start.
- **Start again:** deleting and recreating the app keeps the data, because it is in MySQL. Delete the database as well for a completely fresh library; the starter catalogue and the librarian are recreated on the next start.
- **Backups:** Altare backs up the database nightly and keeps the last 3 days on the free plan. Take a manual backup before an upgrade.

### Troubleshooting

| Problem | What to check |
|---|---|
| Altare cannot pull the image | The package must be public, and the name must be lower case and match exactly. |
| App keeps restarting | Open the app's logs. "Database not ready" means the MySQL variables are missing, so check that the database is connected to the app. |
| "Set a unique SECRET_KEY" in the logs | Add `SHELFORA_SECRET_KEY`. |
| Cannot sign in at `/desk` | Check the `SHELFORA_ADMIN_*` variables; the password must be 10 to 128 characters. |
| Mira or payments unavailable | Check `GROQ_API` and the `RZP_*` keys. |

---

## Artwork and credits

Font and Bootstrap licenses are bundled with their assets in `static/`. Catalogue artwork includes supplied, generated and public-domain material, including Hugh Thomson's 1894 [*Pride and Prejudice* cover](https://commons.wikimedia.org/wiki/File:Pp1894-000-00.jpg). Check the rights and provenance of individual assets before publishing or redistributing them.
