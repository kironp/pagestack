#!/usr/bin/env python3

import argparse
import hashlib
import os
import re
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from pagestack.main import build_epub, read_urls


def file_hash(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def slugify(value: str) -> str:
    return re.sub(r"[^\w\-.]", "_", value).strip("_") or "pagestack"


def resolve_output_path(template: str, title: str) -> str:
    now = datetime.now()
    return template.format(
        title=title,
        slug=slugify(title),
        date=now.strftime("%Y-%m-%d"),
        datetime=now.strftime("%Y%m%d-%H%M%S"),
    )


def load_sent_urls(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()
    lines = state_file.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.strip().startswith("#")}


def save_sent_urls(state_file: Path, sent_urls: set[str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(sorted(sent_urls))
    if content:
        content += "\n"
    state_file.write_text(content, encoding="utf-8")


def send_epub_via_smtp(
    epub_path: Path,
    kindle_email: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    sender_email: str,
    use_starttls: bool,
) -> None:
    message = EmailMessage()
    message["Subject"] = f"PageStack Delivery: {epub_path.name}"
    message["From"] = sender_email
    message["To"] = kindle_email
    message.set_content("Sent by PageStack automation.")

    data = epub_path.read_bytes()
    message.add_attachment(
        data,
        maintype="application",
        subtype="epub+zip",
        filename=epub_path.name,
    )

    if use_starttls:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(message)


def build_and_send(
    urls_file: Path,
    state_file: Path,
    output_template: str,
    title: str,
    author: str,
    timeout: int,
    kindle_email: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    sender_email: str,
    use_starttls: bool,
) -> int | None:
    urls = read_urls(urls_file)
    if not urls:
        print("[error] no URLs found; skipping.")
        return None

    sent_urls = load_sent_urls(state_file)
    new_urls = [url for url in urls if url not in sent_urls]
    if not new_urls:
        print("[skip] no new URLs to export.")
        return None

    print(f"[incremental] {len(new_urls)} new URL(s) out of {len(urls)} total")

    final_title = title or f"Web Articles — {datetime.now().strftime('%Y-%m-%d')}"
    output_path = resolve_output_path(output_template, final_title)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"[build] generating: {output_path}")
    built = build_epub(
        urls=new_urls,
        output_path=output_path,
        title=final_title,
        author=author,
        timeout=timeout,
    )
    if not built:
        return 0

    epub_path = Path(output_path)
    print(f"[send] sending to Kindle: {kindle_email}")
    send_epub_via_smtp(
        epub_path=epub_path,
        kindle_email=kindle_email,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        sender_email=sender_email,
        use_starttls=use_starttls,
    )
    print(f"[done] delivered: {epub_path}")

    if built == len(new_urls):
        sent_urls.update(new_urls)
        save_sent_urls(state_file, sent_urls)
        print(f"[state] updated: {state_file} ({len(sent_urls)} tracked URL(s))")
    else:
        print(
            "[warn] partial build detected; state file not updated to avoid losing unsent URLs."
        )

    return built


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watch a URLs file, rebuild EPUB on changes, and send to Kindle.",
    )
    parser.add_argument(
        "urls_file",
        nargs="?",
        default="kindle-sync/urls_to_be_exported.txt",
        help="Path to text file with one URL per line (default: kindle-sync/urls_to_be_exported.txt)",
    )
    parser.add_argument(
        "--output-template",
        default="kindle-sync/epub/{slug}-{datetime}.epub",
        help="Output template. Supports: {title}, {slug}, {date}, {datetime}",
    )
    parser.add_argument(
        "--state-file",
        default="kindle-sync/.sent_urls.txt",
        help="Path to incremental state file of already sent URLs",
    )
    parser.add_argument("--title", default="", help="Book title")
    parser.add_argument("--author", default="PageStack", help="Author metadata")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    parser.add_argument("--run-once", action="store_true", help="Build/send once, then exit")

    parser.add_argument("--kindle-email", default=os.getenv("PAGESTACK_KINDLE_EMAIL", ""))
    parser.add_argument("--smtp-host", default=os.getenv("PAGESTACK_SMTP_HOST", ""))
    parser.add_argument("--smtp-port", type=int, default=int(os.getenv("PAGESTACK_SMTP_PORT", "587")))
    parser.add_argument("--smtp-user", default=os.getenv("PAGESTACK_SMTP_USER", ""))
    parser.add_argument("--smtp-password", default=os.getenv("PAGESTACK_SMTP_PASSWORD", ""))
    parser.add_argument("--sender-email", default=os.getenv("PAGESTACK_SENDER_EMAIL", ""))
    parser.add_argument(
        "--smtp-starttls",
        action="store_true",
        help="Use STARTTLS (recommended for port 587)",
    )
    args = parser.parse_args()

    urls_file = Path(args.urls_file)
    if not urls_file.exists():
        sys.exit(f"Error: '{urls_file}' not found.")
    state_file = Path(args.state_file)

    required = {
        "kindle_email": args.kindle_email,
        "smtp_host": args.smtp_host,
        "smtp_user": args.smtp_user,
        "smtp_password": args.smtp_password,
    }
    if not all(required.values()):
        missing = [name for name, value in required.items() if not value]
        sys.exit(f"Missing required email settings: {', '.join(missing)}")

    sender_email = args.sender_email or args.smtp_user
    use_starttls = args.smtp_starttls or args.smtp_port == 587

    if args.run_once:
        count = build_and_send(
            urls_file=urls_file,
            state_file=state_file,
            output_template=args.output_template,
            title=args.title,
            author=args.author,
            timeout=args.timeout,
            kindle_email=args.kindle_email,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            smtp_user=args.smtp_user,
            smtp_password=args.smtp_password,
            sender_email=sender_email,
            use_starttls=use_starttls,
        )
        if count == 0:
            sys.exit(1)
        return

    print(f"Watching {urls_file} (interval: {args.interval}s). Press Ctrl+C to stop.")
    last_hash = file_hash(urls_file)
    try:
        while True:
            time.sleep(args.interval)
            current_hash = file_hash(urls_file)
            if current_hash == last_hash:
                continue

            last_hash = current_hash
            print(f"\n[change] detected in {urls_file}")
            try:
                build_and_send(
                    urls_file=urls_file,
                    state_file=state_file,
                    output_template=args.output_template,
                    title=args.title,
                    author=args.author,
                    timeout=args.timeout,
                    kindle_email=args.kindle_email,
                    smtp_host=args.smtp_host,
                    smtp_port=args.smtp_port,
                    smtp_user=args.smtp_user,
                    smtp_password=args.smtp_password,
                    sender_email=sender_email,
                    use_starttls=use_starttls,
                )
            except Exception as exc:
                print(f"[error] build/send failed: {exc}")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()