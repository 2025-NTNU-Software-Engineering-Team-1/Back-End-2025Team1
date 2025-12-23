from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from smtplib import SMTP
from typing import Optional, Iterable, List

import os
import threading
import logging

__all__ = ['send_noreply']

logger = logging.getLogger('gunicorn.error')


def send(
    from_addr: str,
    password: Optional[str],
    to_addrs: List[str],
    subject: str,
    text: str,
    html: str,
):
    SMTP_SERVER = os.environ.get('SMTP_SERVER')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME')

    logger.info(f'[SMTP] Attempting to send email to {to_addrs}')
    logger.info(f'[SMTP] Server: {SMTP_SERVER}:{SMTP_PORT}')
    logger.info(f'[SMTP] From: {from_addr}, Username: {SMTP_USERNAME}')

    if SMTP_SERVER is None:
        logger.warning('[SMTP] SMTP_SERVER is not set, skipping email')
        return

    if from_addr is None:
        logger.error('[SMTP] from_addr is None, skipping email')
        return

    try:
        with SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.set_debuglevel(1)  # 啟用 SMTP debug 輸出
            server.ehlo()
            server.starttls()
            server.ehlo()
            if password is not None:
                username = SMTP_USERNAME if SMTP_USERNAME else from_addr
                logger.info(f'[SMTP] Logging in as: {username}')
                server.login(username, password)
            msg = MIMEMultipart('alternative')
            msg['From'] = from_addr
            msg['To'] = ', '.join(to_addrs)
            msg['Subject'] = subject
            msg.attach(MIMEText(text, 'plain'))
            msg.attach(MIMEText(html, 'html'))
            result = server.send_message(msg, from_addr, to_addrs)
            logger.info(
                f'[SMTP] Email sent successfully to {to_addrs}, result: {result}'
            )
    except Exception as e:
        logger.error(f'[SMTP] Failed to send email: {type(e).__name__}: {e}')
        import traceback
        logger.error(f'[SMTP] Traceback: {traceback.format_exc()}')


def send_noreply(
    to_addrs: Iterable[str],
    subject: str,
    text: str,
    html: Optional[str] = None,
):
    SMTP_NOREPLY = os.environ.get('SMTP_NOREPLY')
    SMTP_NOREPLY_PASSWORD = os.environ.get('SMTP_NOREPLY_PASSWORD')

    # 確保 to_addrs 是 list（避免 iterator 問題）
    to_addrs_list = list(to_addrs)

    logger.info(f'[SMTP] send_noreply called for {to_addrs_list}')

    args = (
        SMTP_NOREPLY,
        SMTP_NOREPLY_PASSWORD,
        to_addrs_list,
        subject,
        text,
        html or text,
    )

    # 使用 daemon=False 確保 thread 完成執行
    t = threading.Thread(target=send, args=args)
    t.daemon = False
    t.start()