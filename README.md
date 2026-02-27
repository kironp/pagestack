PageStack
===

Create an EPUB from a list of URLs so you can read web content offline on your favorite e-reader. I built it to bundle my favorite engineering blogs into books I can load on my Kindle.

## Setup

### Install uv

On macOS:

```bash
brew install uv
```

Or use the official installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Install as a package

```bash
pip install pagestack
```

### Run from source (uv-first)

1. Clone the repository and navigate into it.
2. Create/sync the local environment and install dependencies:

```bash
uv sync
```

3. Run the CLI:

```bash
uv run pagestack kindle-sync/urls_to_be_exported.txt \
    --title "Uber Schemaless - Blogs" \
    --author "Uber Engineering"
```

### Optional pip workflow

If you prefer pip, install dependencies with:

```bash
pip install -r requirements.txt
```

## Usage

1. Add one URL per line in `kindle-sync/urls_to_be_exported.txt`.
2. Run the script (if installed):

```bash
pagestack kindle-sync/urls_to_be_exported.txt \
    --title "Uber Schemaless - Blogs" \
    --author "Uber Engineering"
```

If you do not pass any parameter and just run `pagestack`, it reads
`kindle-sync/urls_to_be_exported.txt` and writes output under
`kindle-sync/epub`.

3. If you have installed it from source with pip, you can run:

```bash
python src/pagestack/main.py kindle-sync/urls_to_be_exported.txt \
    --title "Uber Schemaless - Blogs" \
    --author "Uber Engineering"
```

## Automation: watch `kindle-sync/urls_to_be_exported.txt` and auto-send to Kindle

Set your Kindle + SMTP credentials (example shown for shell env vars):

```bash
export PAGESTACK_KINDLE_EMAIL="your_kindle_id@kindle.com"
export PAGESTACK_SMTP_HOST="smtp.gmail.com"
export PAGESTACK_SMTP_PORT="587"
export PAGESTACK_SMTP_USER="you@example.com"
export PAGESTACK_SMTP_PASSWORD="your_app_password"
export PAGESTACK_SENDER_EMAIL="you@example.com"
```

Run once (build + send):

```bash
uv run pagestack-watch kindle-sync/urls_to_be_exported.txt --run-once --title "Uber Engineering" --author "Uber"
```

Run in watch mode (rebuild + resend whenever the file changes):

```bash
uv run pagestack-watch kindle-sync/urls_to_be_exported.txt --title "Uber Engineering" --author "Uber"
```

`pagestack-watch` is incremental: it tracks URLs already sent in
`kindle-sync/.sent_urls.txt` and only builds/sends newly added URLs.

By default, output files are timestamped as `kindle-sync/epub/{slug}-{datetime}.epub`.
You can change this with:

```bash
uv run pagestack-watch kindle-sync/urls_to_be_exported.txt --output-template "kindle-sync/epub/uber-{date}.epub"
```

You can override the state file path with:

```bash
uv run pagestack-watch --state-file kindle-sync/.sent_urls.txt
```

To force re-exporting all URLs, remove the state file:

```bash
rm -f kindle-sync/.sent_urls.txt
```

Before sending to Kindle, make sure your sender email is approved in Amazon
"Personal Document Settings".

## Locking dependencies

Use `uv` lockfile management so environments are reproducible:

```bash
uv lock
```

Commit `uv.lock` to version control.

## Publishing on PyPi

```bash
uv sync --group dev
uv build
uv publish
```

## License

MIT
