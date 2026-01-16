# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import random
import requests
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    CallbackQueryHandler,
    filters
)
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
GAS_URL_ENV = os.environ.get('GAS_URL')

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

def get_gas_url():
    bot_id = TOKEN.split(':')[0]
    stored_url = db.reference(f'bot_configs/{bot_id}/gas_url').get()
    return stored_url if stored_url else GAS_URL_ENV

# --- API Caller ---
def call_gas_api(payload):
    url = get_gas_url()
    if not url:
        return {"status": "error", "message": "GAS URL পাওয়া যায়নি।"}
    try:
        response = requests.post(url, json=payload, timeout=35)
        return response.json() if response.status_code == 200 else {"status": "error", "message": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- Menu Builder ---
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚀 পাঠানো শুরু করুন", callback_data='btn_start_send'),
         InlineKeyboardButton("🛑 পাঠানো বন্ধ করুন", callback_data='btn_stop_send')],
        [InlineKeyboardButton("📝 ইমেইল কন্টেন্ট সেট", callback_data='btn_set_content'),
         InlineKeyboardButton("📊 লাইভ রিপোর্ট", callback_data='btn_stats')],
        [InlineKeyboardButton("🔋 লিমিট চেক", callback_data='btn_limit'),
         InlineKeyboardButton("🔗 GAS URL আপডেট", callback_data='btn_update_gas')],
        [InlineKeyboardButton("🔄 ডাটা রিসেট", callback_data='btn_reset_all')]
    ]
    return InlineKeyboardMarkup(keyboard)

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data='btn_main_menu')]])

# --- Background Worker ---
async def email_worker(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    bot_id = TOKEN.split(':')[0]
    
    config = db.reference('shared_config/email_template').get()
    if not config:
        await context.bot.send_message(chat_id, "⚠️ ইমেইল টেমপ্লেট সেট করা নেই! /set_email ব্যবহার করুন।")
        IS_SENDING = False
        return

    leads_ref = db.reference('scraped_emails')
    count = 0

    while IS_SENDING:
        all_leads = leads_ref.get()
        if not all_leads: break
        
        target_key = None
        target_data = None
        
        for k, v in all_leads.items():
            if v.get('status') is None and v.get('processing_by') is None:
                target_key = k
                target_data = v
                break
        
        if not target_key:
            await context.bot.send_message(chat_id, "🏁 ডাটাবেজে আর কোনো নতুন লিড নেই।")
            break

        # Lock the lead
        leads_ref.child(target_key).update({'processing_by': bot_id})
        
        email = target_data.get('email')
        app_name = target_data.get('app_name', 'Developer')
        sub = config['subject'].replace('{app_name}', app_name)
        body = config['body'].replace('{app_name}', app_name)

        res = call_gas_api({"action": "sendEmail", "to": email, "subject": sub, "body": body})
        
        if res.get("status") == "success":
            leads_ref.child(target_key).update({
                'status': 'sent', 
                'sent_at': datetime.now().isoformat(),
                'sent_by': bot_id,
                'processing_by': None
            })
            count += 1
            if count % 10 == 0:
                await context.bot.send_message(chat_id, f"✅ সফলভাবে {count}টি মেইল পাঠানো হয়েছে।")
                await asyncio.sleep(random.randint(60, 150))
        else:
            leads_ref.child(target_key).update({'processing_by': None})
            msg = res.get('message', '').lower()
            if "limit" in msg or "quota" in msg:
                await context.bot.send_message(chat_id, "🚨 এই জিমেইলের লিমিট শেষ! /update_gas দিয়ে নতুন লিঙ্ক দিন।")
                IS_SENDING = False
                break
        
        await asyncio.sleep(random.randint(45, 90))

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 সেশন শেষ। মোট পাঠানো হয়েছে: {count}")

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text("🤖 **ইমেইল মার্কেটিং কন্ট্রোল প্যানেল**", 
                                   reply_markup=main_menu_keyboard(), parse_mode="Markdown")

async def button_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    query = update.callback_query
    await query.answer()
    
    if query.data == 'btn_main_menu':
        await query.edit_message_text("🤖 **ইমেইল মার্কেটিং কন্ট্রোল প্যানেল**", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    
    elif query.data == 'btn_start_send':
        if IS_SENDING:
            await query.edit_message_text("⚠️ অলরেডি পাঠানো হচ্ছে।", reply_markup=back_button())
        else:
            IS_SENDING = True
            context.job_queue.run_once(email_worker, 1, chat_id=query.message.chat_id)
            await query.edit_message_text("🚀 কিউ প্রসেস শুরু হয়েছে...", reply_markup=back_button())
            
    elif query.data == 'btn_stop_send':
        IS_SENDING = False
        await query.edit_message_text("🛑 পাঠানো বন্ধ করার রিকোয়েস্ট নেওয়া হয়েছে।", reply_markup=back_button())
        
    elif query.data == 'btn_stats':
        leads = db.reference('scraped_emails').get() or {}
        total = len(leads)
        sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
        await query.edit_message_text(f"📊 **লাইভ রিপোর্ট:**\n\nমোট লিড: {total}\n✅ পাঠানো হয়েছে: {sent}\n⏳ বাকি আছে: {total-sent}", 
                                     reply_markup=back_button())
    
    elif query.data == 'btn_limit':
        await query.edit_message_text("⏳ লিমিট চেক করা হচ্ছে...")
        res = call_gas_api({"action": "getLimit"})
        rem = res.get("remaining", "Unknown")
        await query.edit_message_text(f"📉 বর্তমান জিমেইলের অবশিষ্ট লিমিট: **{rem}**", 
                                     reply_markup=back_button(), parse_mode="Markdown")

    elif query.data == 'btn_set_content':
        await query.edit_message_text("📝 ইমেইল সেট করতে `/set_email` কমান্ডটি ব্যবহার করুন।\n\nফরম্যাট: `Subject | Body`", 
                                     reply_markup=back_button())

    elif query.data == 'btn_update_gas':
        await query.edit_message_text("🔗 লিঙ্ক আপডেট করতে `/update_gas [URL]` কমান্ডটি ব্যবহার করুন।", 
                                     reply_markup=back_button())
    
    elif query.data == 'btn_reset_all':
        await query.edit_message_text("⚠️ আপনি কি সব 'Sent' স্ট্যাটাস রিসেট করতে চান?\n\nনিশ্চিত হলে লিখুন: `/confirm_reset`", 
                                     reply_markup=back_button())

async def update_gas_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    if not c.args:
        await u.message.reply_text("⚠️ ব্যবহার: `/update_gas https://...`")
        return
    bot_id = TOKEN.split(':')[0]
    db.reference(f'bot_configs/{bot_id}/gas_url').set(c.args[0])
    await u.message.reply_text("✅ এই বটের জন্য নতুন GAS URL সেভ করা হয়েছে।")

async def set_email_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    try:
        content = u.message.text.split('/set_email ', 1)[1]
        sub, body = content.split('|', 1)
        db.reference('shared_config/email_template').set({'subject': sub.strip(), 'body': body.strip()})
        await u.message.reply_text("✅ সেন্ট্রাল ইমেইল টেমপ্লেট আপডেট হয়েছে।")
    except:
        await u.message.reply_text("❌ ভুল ফরম্যাট! সঠিক নিয়ম: `/set_email Subject | Body`")

async def confirm_reset_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    leads = db.reference('scraped_emails').get() or {}
    for k in leads:
        db.reference(f'scraped_emails/{k}').update({'status': None, 'processing_by': None, 'sent_by': None})
    await u.message.reply_text("🔄 ডাটাবেজ রিসেট সম্পন্ন। সব বট এখন আবার শুরু থেকে কাজ করতে পারবে।")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update_gas", update_gas_cmd))
    app.add_handler(CommandHandler("set_email", set_email_cmd))
    app.add_handler(CommandHandler("confirm_reset", confirm_reset_cmd))
    app.add_handler(CallbackQueryHandler(button_tap))

    if RENDER_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN[-10:], 
                        webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
