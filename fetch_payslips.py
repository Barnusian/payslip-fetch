import imaplib
import email
from email.policy import default
from pathlib import Path
import subprocess
import logging
import os
import time
from datetime import datetime, time as dtime, timedelta

# ----------------------------
# Configuration (via env vars)
# ----------------------------

IMAP_SERVER = "imap.gmail.com"

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

SENDER_EMAIL = os.environ["SENDER_EMAIL"]
GMAIL_LABEL = os.environ["GMAIL_LABEL"]
GMAIL_PROCESSED_LABEL = os.environ.get("GMAIL_PROCESSED_LABEL", "Payslips/Processed")

PDF_PASSWORD = os.environ["PDF_PASSWORD"]

CONSUME_DIR = Path("/consume")
TMP_DIR = Path("/tmp/payslips")

# Scheduling rules
CHECK_INTERVAL_SECONDS = 2 * 60 * 60  # 2 hours
WINDOW_START = dtime(10, 0)           # 10:00
WINDOW_END = dtime(23, 59)            # midnight-ish
VALID_WEEKDAYS = {1, 2, 3}            # Tue=1, Wed=2, Thu=3 (datetime.weekday)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

TMP_DIR.mkdir(exist_ok=True)


# ----------------------------
# Utility: sleep calculation
# ----------------------------

def sleep_until(target: datetime):
    seconds = max(0, (target - datetime.now()).total_seconds())
    logging.info(f"Sleeping until {target}")
    time.sleep(seconds)


def next_tuesday_at_10(now: datetime) -> datetime:
    days_ahead = (1 - now.weekday()) % 7
    if days_ahead == 0 and now.time() >= WINDOW_START:
        days_ahead = 7
    return datetime.combine(
        now.date() + timedelta(days=days_ahead),
        WINDOW_START
    )


def next_valid_wakeup(now: datetime) -> datetime:
    # Outside Tue–Thu
    if now.weekday() not in VALID_WEEKDAYS:
        return next_tuesday_at_10(now)

    # Before daily window
    if now.time() < WINDOW_START:
        return datetime.combine(now.date(), WINDOW_START)

    # After daily window
    if now.time() > WINDOW_END:
        return datetime.combine(now.date() + timedelta(days=1), WINDOW_START)

    # Inside window → short sleep
    return now + timedelta(seconds=CHECK_INTERVAL_SECONDS)


# ----------------------------
# PDF handling
# ----------------------------

def decrypt_pdf(input_pdf: Path, output_pdf: Path):
    subprocess.run(
        [
            "qpdf",
            f"--password={PDF_PASSWORD}",
            "--decrypt",
            str(input_pdf),
            str(output_pdf)
        ],
        check=True
    )


# ----------------------------
# Mail processing
# ----------------------------

def process_mailbox() -> bool:
    """
    Returns True if at least one payslip was successfully processed.
    """

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(GMAIL_USER, GMAIL_PASSWORD)

    # Only search inside the Payslips label
    mail.select(f'"{GMAIL_LABEL}"')

    status, messages = mail.search(
        None,
        f'(FROM "{SENDER_EMAIL}")'
    )

    if status != "OK":
        logging.error("Mail search failed")
        mail.logout()
        return False

    # Newest first
    msg_ids = messages[0].split()[::-1]

    for msg_id in msg_ids:
        _, data = mail.fetch(msg_id, "(RFC822)")
        msg = email.message_from_bytes(data[0][1], policy=default)

        for part in msg.iter_attachments():
            filename = part.get_filename()
            if not filename or not filename.lower().endswith(".pdf"):
                continue

            encrypted = TMP_DIR / filename
            decrypted = CONSUME_DIR / filename

            encrypted.write_bytes(part.get_payload(decode=True))

            try:
                decrypt_pdf(encrypted, decrypted)
                encrypted.unlink()

                # Apply "Processed" label
                mail.store(msg_id, "+X-GM-LABELS", f'"{GMAIL_PROCESSED_LABEL}"')

                logging.info(f"Imported and processed {filename}")
                mail.logout()
                return True

            except Exception as e:
                logging.error(f"Failed to process {filename}: {e}")

    mail.logout()
    return False


# ----------------------------
# Main loop
# ----------------------------

while True:
    now = datetime.now()

    if now.weekday() in VALID_WEEKDAYS and WINDOW_START <= now.time() <= WINDOW_END:
        logging.info("Within processing window, checking mailbox")
        success = process_mailbox()

        if success:
            # Go dormant until next week
            sleep_until(next_tuesday_at_10(datetime.now()))
            continue

    # Nothing to do right now
    sleep_until(next_valid_wakeup(datetime.now()))
