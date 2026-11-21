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

> **Why a VPS and not a paid platform?** This is a mini-project with a limited scope, so we host it on a VPS we already own (an Oracle Cloud Always Free ARM server) instead of a managed platform. The few extra pieces we added for that are a `Dockerfile`, a short start script, a GitHub Actions workflow that builds the image, a small Docker Compose file, and a free DuckDNS subdomain for the address. The same steps work on **any VPS** that can run Docker. In a real-world project we would weigh the options properly and host on a paid plan of a managed platform, with managed databases, backups, uptime guarantees and a custom domain.

How it fits together:

```
git push  ->  GitHub Actions builds the image  ->  ghcr.io/colorblakc/shelfora:latest
                                                          |
your VPS:  Docker Compose runs Shelfora + MySQL  ->  nginx (HTTPS, certbot)  ->  https://yourname.duckdns.org
                                                          ^
                                   DuckDNS container keeps the name pointed at the VPS
```

- **The image** is built for both Intel/AMD and ARM servers on every push to `main` (`.github/workflows/docker.yml`) and published as a public package, so the server needs no login to pull it.
- **MySQL** runs beside the app in its own container on a private Docker network; its data lives in a Docker volume on the VPS disk.
- **On start** the container waits for the database, runs `flask init-db` (creates or upgrades the tables and adds the starter catalogue, safe to repeat) and `flask ensure-admin` (creates the librarian from `SHELFORA_ADMIN_*` if missing) (`docker-entrypoint.sh`).

### What you need

- A VPS with Docker and Docker Compose (Ubuntu or Debian is easiest). Install Docker with `curl -fsSL https://get.docker.com | sudo sh`.
- Ports **80 and 443** open to the internet, in the cloud provider's firewall and in the server's own firewall. On Oracle Cloud that means an ingress rule in the VCN security list plus `iptables` rules on the instance.
- A free [DuckDNS](https://www.duckdns.org) account.

### 1. Claim a DuckDNS name

Sign in at duckdns.org, add a name (for example `shelfora`), and copy your **token** from the top of the page. DuckDNS names are shared by everyone, so pick one that is free.

### 2. Get the deployment files onto the server

```bash
git clone https://github.com/colorblakc/shelfora.git
cd shelfora/deploy
cp .env.example .env
cp shelfora.env.example shelfora.env
```

Edit the two files (`nano .env`, `nano shelfora.env`):

| File | What to set |
|---|---|
| `.env` | `DUCKDNS_SUBDOMAIN`, `DUCKDNS_TOKEN`, and two long random database passwords |
| `shelfora.env` | `SHELFORA_SECRET_KEY` (a long random secret), the `SHELFORA_ADMIN_*` librarian account, plus optional `GROQ_API` and `RZP_*` keys |

Keep both files private; they are git-ignored. See [Configuration](#configuration) for every setting.

### 3. Start the app

```bash
sudo docker compose up -d
sudo docker compose logs -f shelfora      # Ctrl+C to leave the logs
```

The app now answers on `127.0.0.1:8002` and the DuckDNS container points your name at the server.

### 4. Put HTTPS in front of it (nginx and certbot)

If the server has no web server yet, run `sudo apt-get install -y nginx certbot python3-certbot-nginx`. Then create `/etc/nginx/conf.d/shelfora.conf`, replacing the host name:

```nginx
server {
    listen 80;
    server_name yourname.duckdns.org;
    client_max_body_size 10m;
    location / {
        proxy_pass http://127.0.0.1:8002;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d yourname.duckdns.org --redirect
```

certbot issues the free certificate and renews it automatically. A server that already runs nginx for other sites can simply have this file added; nothing else changes. If you prefer Caddy, a two-line `Caddyfile` does the same: `yourname.duckdns.org { reverse_proxy 127.0.0.1:8002 }`.

### 5. Check that it works

- Open `https://yourname.duckdns.org`, browse the catalogue, create a member account and reserve a book.
- Sign in as the librarian at `/desk`, approve the request, issue the book and record its return.
- Book a reading-room seat, and ask Mira a question if `GROQ_API` is set.

### Updating, backups and starting again

- **Update:** push to `main`, wait for the **Actions** tab to show the image built, then on the server run `sudo docker compose pull && sudo docker compose up -d`. `init-db` upgrades the tables on start and the data volume is kept.
- **Back up** the database before an upgrade: `sudo docker compose exec db sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" shelfora' > shelfora-backup.sql`.
- **Start again:** `sudo docker compose down -v` deletes the database volume; the next `up` recreates the tables, the starter catalogue and the librarian.
- **Reminder emails** are not part of this deployment.

### Troubleshooting

| Problem | What to check |
|---|---|
| The address does not load | `sudo docker compose ps`, and make sure ports 80 and 443 are open in both firewalls. |
| The app keeps restarting | `sudo docker compose logs shelfora`. "Database not ready" for more than a minute means the `db` container is unhealthy; check `sudo docker compose logs db`. |
| "Set a unique SECRET_KEY" in the logs | Add `SHELFORA_SECRET_KEY` to `deploy/shelfora.env`. |
| Cannot sign in at `/desk` | Check the `SHELFORA_ADMIN_*` values; the password must be 10 to 128 characters. They only apply the first time an account is created. |
| certbot fails | The DuckDNS name must already point at the server. Check with `getent hosts yourname.duckdns.org` and the DuckDNS container logs. |
| The DuckDNS container exits | `DUCKDNS_TOKEN` or `DUCKDNS_SUBDOMAIN` is empty in `deploy/.env`. |
| Mira or payments unavailable | Check `GROQ_API` and the `RZP_*` keys. |

---

## Artwork and credits

Font and Bootstrap licenses are bundled with their assets in `static/`. Catalogue artwork includes supplied, generated and public-domain material, including Hugh Thomson's 1894 [*Pride and Prejudice* cover](https://commons.wikimedia.org/wiki/File:Pp1894-000-00.jpg). Check the rights and provenance of individual assets before publishing or redistributing them.
