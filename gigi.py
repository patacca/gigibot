#!/bin/env python3

from telegram.ext import Updater, CommandHandler, ConversationHandler, MessageHandler, Filters
import telegram
from config import BOT_TOKEN, LOG_FILE
import requests, re, logging, time, sys, datetime, time

BASE_URL = 'https://vaccinicovid.regione.veneto.it'
headers = {
	'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; rv:71.0) Gecko/20100101 Firefox/71.0'
}
CF_STATE, PASSWORD_STATE, SETRANGE_STATE = range(3)

# shared variable
accounts = {}
globalAlreadyFree = {}
_session = None

def getSession():
	global _session
	
	if not _session:
		_session = requests.sessions.Session()
		_session.headers.update(headers)
		return _session
	return _session

def daemonRun(context):
	chatId = context.job.context['chatId']
	session = getSession()
	
	logger.debug('Getting availability...')
	r = session.post(BASE_URL + '/ulss9/azione/controllocf', data={'cod_fiscale':accounts[chatId]['cf'], 'num_tessera':accounts[chatId]['password']})
	if not r.status_code == 200: # Error
		logger.error(f'While logging in there was an error: {r.status_code} HTTP')
		logger.debug(f'ERROR CONTENT: {r.text}')
		return
	r = session.get(BASE_URL + '/ulss9/azione/sceglisede/servizio/746')
	if not r.status_code == 200: # Error
		logger.error(f'While getting availability there was an error: {r.status_code} HTTP')
		logger.debug(f'ERROR CONTENT: {r.text}')
		return
	
	matches = re.findall('<button class="btn btn-primary btn-full"(.*?)>(.*?)</button>', r.text)
	
	alreadyFree = globalAlreadyFree[chatId]
	# Unchecking all the elements
	for k in alreadyFree:
		alreadyFree[k]['checked'] = False
	
	for extra,m in matches:
		if 'DISPONIBILITA ESAURITA' in m:
			continue
		alreadyFree.setdefault(m, {})
		alreadyFree[m]['checked'] = True
		
		id1, id2 = re.match('.*act_step\(([0-9]*),([0-9]*)\).*', extra).groups()
		
		startDate = datetime.datetime.strftime(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=30), "%Y-%m-%dT%H:%M:%S+02:00")
		endDate = datetime.datetime.strftime(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=60), "%Y-%m-%dT%H:%M:%S+02:00")
		r2 = session.post(BASE_URL + '/ulss9', data={'azione':'jscalendario', 'servizio':746, 'sede':id2, 'start':startDate, 'end':endDate})
		
		logger.info(f'Chat {chatId} found one free spot with id({id1},{id2})')
		logger.debug(f'free spot content: {r2.text}')
		data = r2.json()
		logger.debug(f'free spot day content: {data}')
		
		# Unchecking dates
		for k in alreadyFree[m]:
			if k != 'checked':
				alreadyFree[m][k] = False
		
		# each element is a single free slot
		for freeSlot in data:
			dateStart = freeSlot['start']
			
			# Check if it is within the user time range
			if (
				dateStart < accounts[chatId]['startDate'].strftime('%Y-%m-%d') or
				dateStart > accounts[chatId]['endDate'].strftime('%Y-%m-%d')
			):
				continue
			
			# Check if it has already been notified
			if dateStart in alreadyFree[m]:
				alreadyFree[m][dateStart] = True
				continue
			alreadyFree[m][dateStart] = True
			
			context.bot.send_message(chat_id=chatId, text=f'Hurry up! There is one free spot in date {dateStart}\nin center `{m}`', parse_mode=telegram.ParseMode.MARKDOWN)
		
		# Remove unchecked dates
		for k,v in list(alreadyFree[m].items()):
			if k != 'checked' and not v:
				alreadyFree[m].pop(k)
	
	# Remove elements that were not checked in this round
	for k,v in list(alreadyFree.items()):
		if not v['checked']:
			alreadyFree.pop(k)

def parseDateRange(dateRangeStr):
	currYear = str(datetime.datetime.now().year)
	dateRange = dateRangeStr.strip().split('_')
	if len(dateRange) != 2:
		return (None, None)
	
	datetimeList = []
	for date in dateRange:
		try:
			datetimeList.append(datetime.datetime.strptime(currYear+'-'+date, '%Y-%m-%d'))
		except:
			return (None, None)
	
	return datetimeList

def start(update, context):
	if update.effective_chat.id in accounts:
		update.message.reply_text("You are already receiving the notifications")
		return ConversationHandler.END
	
	accounts[update.effective_chat.id] = {}
	logger.debug(f'Chat {update.effective_chat.id}: started')
	update.message.reply_text(
		"Welcome to gigi, the notification bot for booking a covid19 vaccine in Veneto\n"
		"Write your CODICE FISCALE"
	)
	
	return CF_STATE

def cf(update, context):
	cfValue = update.message.text
	logger.debug(f'Chat {update.effective_chat.id}: got CF {cfValue}')
	accounts[update.effective_chat.id]['cf'] = cfValue
	update.message.reply_text("Now write the last 6 digits of your TESSERA SANITARIA")
	
	return PASSWORD_STATE

def password(update, context):
	passwordValue = update.message.text
	logger.debug(f'Chat {update.effective_chat.id}: got password {passwordValue}')
	accounts[update.effective_chat.id]['password'] = passwordValue
	
	update.message.reply_text("Now you have to specify for which time range you are searching.")
	update.message.reply_text("Write the starting date and the ending date (both included) (they both can be either in the future or in the past)")
	update.message.reply_text("The format <b>must</b> be the following: <b>mm-dd_mm-dd</b>", parse_mode=telegram.ParseMode.HTML)
	update.message.reply_text("The first is the starting date and the latter is the ending date")
	
	return SETRANGE_STATE

def setRange(update, context):
	startDate, endDate = parseDateRange(update.message.text)
	if startDate is None:
		update.message.reply_text("The date range you entered is in a wrong format. Please send it again in the right format.")
		return SETRANGE_STATE
	
	logger.debug(f'Chat {update.effective_chat.id}: got timerange ({startDate.strftime("%Y-%m-%d")} {endDate.strftime("%Y-%m-%d")})')
	
	accounts[update.effective_chat.id]['startDate'] = startDate
	accounts[update.effective_chat.id]['endDate'] = endDate
	
	update.message.reply_text(f'Perfect! You will receive notifications from {startDate.strftime("%Y-%m-%d")} to {endDate.strftime("%Y-%m-%d")}')
	update.message.reply_text(f'To stop the bot just send the command /stop')
	
	globalAlreadyFree[update.effective_chat.id] = {}
	context.job_queue.run_repeating(daemonRun, 2, context={'chatId': update.effective_chat.id}, name=str(update.effective_chat.id))
	
	return ConversationHandler.END

def cancel(update, context):
	accounts.pop(update.effective_chat.id)
	logger.debug(f'Chat {update.effective_chat.id}: cancel conversation')
	update.message.reply_text('Bye!')
	return ConversationHandler.END

def stop(update, context):
	try:
		accounts.pop(update.effective_chat.id)
	except:
		pass
	logger.debug(f'Chat {update.effective_chat.id}: bot stopped')
	update.message.reply_text('Stopping now. Bye!')
	# ~ context.job_queue.stop()
	job = context.job_queue.get_jobs_by_name(str(update.effective_chat.id))
	if len(job) == 1:
		job[0].schedule_removal()

if __name__ == '__main__':
	logger = logging.getLogger(__name__)
	logger.setLevel(logging.DEBUG)
	handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1000*1000*1, backupCount=5)
	formatter = logging.Formatter('%(asctime)s - [%(levelname)s]  %(message)s')
	handler.setFormatter(formatter)
	logger.addHandler(handler)
	
	logger.info('gigi bot is starting')
	
	updater = Updater(token=BOT_TOKEN, use_context=True)
	dispatcher = updater.dispatcher
	
	conversationHandler = ConversationHandler(
		entry_points=[CommandHandler('start', start)],
		states={
			CF_STATE: [MessageHandler(Filters.text & ~Filters.command, cf)],
			PASSWORD_STATE: [MessageHandler(Filters.text & ~Filters.command, password)],
			SETRANGE_STATE: [MessageHandler(Filters.text & ~Filters.command, setRange)],
		},
		fallbacks=[CommandHandler('cancel', cancel)],
	)
	dispatcher.add_handler(conversationHandler)
	dispatcher.add_handler(CommandHandler("stop", stop))
	
	updater.start_polling()
	updater.idle()
