# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import random
import requests
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
import firebase_admin
from firebase_admin import credentials, db

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TOKEN = os.environ.get('EMAIL_BOT_TOKEN')
OWNER_ID = os.environ.get('BOT_OWNER_ID')
FB_JSON = os.environ.get('FIREBASE_CREDENTIALS_JSON')
FB_URL = os.environ.get('FIREBASE_DATABASE_URL')
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')
PORT = int(os.environ.get('PORT', '10000'))

# Google Apps Script Web App URL (Render-এ GAS_URL নামে সেভ করুন অথবা এখানে সরাসরি বসান)
GAS_URL = os.environ.get('GAS_URL') # https://script.google.com/macros/s/.../exec

# --- Global Control ---
IS_SENDING = False

# --- Firebase Initialization ---
try:
    if not firebase_admin._apps:
        cred_dict = json.loads(FB_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FB_URL})
    logger.info("🔥 Firebase Connected!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")

def is_owner(uid):
    return str(uid) == str(OWNER_ID)

# --- Send via Google Apps Script (HTTP Request) ---
def send_email_via_gas(to_email, subject, body_html):
    try:
        payload = {
            "to": to_email,
            "subject": subject,
            "body": body_html
        }
        # গুগল স্ক্রিপ্টে রিকোয়েস্ট পাঠানো
        response = requests.post(GAS_URL, json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            return result.get("status") == "success"
        else:
            logger.error(f"GAS Error Status: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ GAS Connection Error: {e}")
        return False

# --- Background Human-like Processor ---
async def process_email_queue(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    
    config = db.reference('email_config').get()
    if not config or 'subject' not in config:
        await context.bot.send_message(chat_id, "⚠️ Content set করা নেই!")
        IS_SENDING = False
        return

    ref = db.reference('scraped_emails')
    all_leads = ref.get()

    if not all_leads:
        await context.bot.send_message(chat_id, "❌ ডাটাবেজে লিড নেই।")
        IS_SENDING = False
        return

    count = 0
    await context.bot.send_message(chat_id, "🚀 Google Apps Script এর মাধ্যমে মানুষের মতো পাঠানো শুরু হয়েছে।")

    for key, data in all_leads.items():
        if not IS_SENDING: break
        if data.get('status') == 'sent': continue

        email = data.get('email')
        app_name = data.get('app_name', 'Developer')
        
        final_subject = config['subject'].replace('{app_name}', app_name)
        final_body = config['body'].replace('{app_name}', app_name)

        if send_email_via_gas(email, final_subject, final_body):
            ref.child(key).update({
                'status': 'sent', 
                'sent_at': datetime.now().isoformat(),
                'method': 'google_script'
            })
            count += 1
            if count % 10 == 0:
                await context.bot.send_message(chat_id, f"✅ সফলভাবে {count} টি পাঠানো হয়েছে।")
                await asyncio.sleep(random.randint(60, 120))
        
        # প্রতিটি মেইলের মাঝে ৩০-৬০ সেকেন্ডের হিউম্যান গ্যাপ
        await asyncio.sleep(random.randint(30, 60))

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 কাজ শেষ! সেশনে পাঠানো হয়েছে: {count}")

# --- Commands ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_owner(u.effective_user.id):
        await u.message.reply_text("🤖 Google Script Bot Online!\n/set_content\n/start_sending\n/stats")

async def stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    leads = db.reference('scraped_emails').get() or {}
    total = len(leads)
    sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
    await u.message.reply_text(f"📊 মোট: {total}\n✅ পাঠানো: {sent}\n⏳ বাকি: {total-sent}")

async def start_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if not is_owner(u.effective_user.id): return
    if IS_SENDING:
        await u.message.reply_text("⚠️ অলরেডি পাঠানো হচ্ছে।")
        return
    IS_SENDING = True
    c.job_queue.run_once(process_email_queue, 1, chat_id=u.effective_chat.id)
    await u.message.reply_text("🚀 কিউতে যুক্ত করা হয়েছে।")

async def stop_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if not is_owner(u.effective_user.id): return
    IS_SENDING = False
    await u.message.reply_text("🛑 থামানো হচ্ছে...")

# --- Conversation ---
SUBJECT, BODY = range(2)
async def set_c(u, c): 
    if not is_owner(u.effective_user.id): return
    await u.message.reply_text("Subject:"); return SUBJECT
async def set_s(u, c):
    c.user_data['ts'] = u.message.text
    await u.message.reply_text("Body (HTML):"); return BODY
async def set_b(u, c):
    db.reference('email_config').set({'subject': c.user_data['ts'], 'body': u.message.text})
    await u.message.reply_text("✅ Saved!"); return ConversationHandler.END

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("start_sending", start_sending))
    app.add_handler(CommandHandler("stop_sending", stop_sending))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('set_content', set_c)],
        states={SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_s)],
                BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_b)]},
        fallbacks=[]
    ))
    if RENDER_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN[-10:], webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
