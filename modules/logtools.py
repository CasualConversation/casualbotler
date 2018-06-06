#!/usr/bin/env python3
'''Stuff about making the spreadsheet information a bit more useful'''

import shlex
import re
import os
import argparse
import subprocess
import urllib
import time
import requests
from sopel import module
import sopel.tools
from sopel.config.types import StaticSection, ListAttribute, ValidatedAttribute, FilenameAttribute, NO_DEFAULT
from apiclient.discovery import build
from fuzzywuzzy import fuzz


class LogToolsSection(StaticSection):
    google_api_key_password = ValidatedAttribute('google_api_key_password')
    admin_channels = ListAttribute('admin_channels')
    acceptable_fuzz_ratio = ValidatedAttribute('acceptable_fuzz_ratio', int, default=75)
    spreadsheet_id = ValidatedAttribute('spreadsheet_id')
    relevant_sheets = ListAttribute('relevant_sheets')
    relevant_range = ValidatedAttribute('relevant_range')

def configure(config):
    config.define_section('logtools', LogToolsSection, validate=True)


parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('term', type=str, help='the nick or piece of mask to search')

def setup(bot):
    bot.config.define_section('logtools', LogToolsSection, validate=True)
    bot.memory['google_sheets_service'] = build('sheets', 'v4', developerKey=bot.config.logtools.google_api_key_password, cache_discovery=False)
    if 'sheet_content_1' in bot.memory:
        del bot.memory['sheet_content_1']
    if 'sheet_content_2' in bot.memory:
        del bot.memory['sheet_content_2']

ACCEPTABLE_RATIO = 75

@module.commands('search')
def search(bot, trigger):

    extra_info = ''

    isAdminChannel = (trigger.sender in bot.config.logtools.admin_channels)
    if not isAdminChannel:
        return

    arguments = trigger.groups()[1]
    if arguments is None:
        bot.reply('No arguments :(   To learn the command syntax, please use -h')
        return
    try:
        args = parser.parse_args(shlex.split(arguments))
    except SystemExit as e:
        if '-h' in arguments or '--help' in arguments:
            helpsearch(bot, trigger)
        else:
            bot.reply('invalid arguments :(   To learn the command syntax, please use -h')
        return

    if 'sheet_content_1' not in bot.memory:
        refresh_spreadsheet_content(bot)

    found_indexes_1 = []
    found_indexes_2 = []

    for index, line in enumerate(bot.memory['sheet_content_1']):
        if line and (args.term in line[8] or fuzz.ratio(args.term.lower(), line[1].lower()) >= ACCEPTABLE_RATIO):
            found_indexes_1.append(index)

    for index, line in enumerate(bot.memory['sheet_content_2']):
        if line and (args.term in line[8] or fuzz.ratio(args.term.lower(), line[1].lower()) >= ACCEPTABLE_RATIO):
            found_indexes_2.append(index)

    sheet_1_instances = []
    sheet_2_instances = []
    for an_index in found_indexes_1:
        relevant_row = bot.memory['sheet_content_1'][an_index]
        sheet_1_instances.append('{} on {} ({}) (row {})'.format(relevant_row[2], relevant_row[1], relevant_row[8], an_index+1+1))  # one for 1-index, one for missing row
    for an_index in found_indexes_2:
        relevant_row = bot.memory['sheet_content_2'][an_index]
        sheet_2_instances.append('{} on {} ({}) (row {} (old sheet))'.format(relevant_row[2], relevant_row[1], relevant_row[8], an_index+1+1))

    instances = sheet_1_instances + sheet_2_instances

    if len(instances) > 3:
        bot.reply('\U0001F914 ' + create_snoonet_paste('\n'.join(instances)))
    else:
        bot.reply(',     '.join(instances))


RELEVANT_SHEETS = ('Operator Actions', 'Pre 2018 Data')
RELEVANT_RANGE = 'a2:k'
@module.interval(300)
def refresh_spreadsheet_content(bot):
    bot.memory['sheet_content_1'] = bot.memory['google_sheets_service'].spreadsheets().values().get(spreadsheetId=bot.config.logtools.spreadsheet_id, range=RELEVANT_SHEETS[0]+'!'+RELEVANT_RANGE).execute().get('values', [])
    time.sleep(1)
    bot.memory['sheet_content_2'] = bot.memory['google_sheets_service'].spreadsheets().values().get(spreadsheetId=bot.config.logtools.spreadsheet_id, range=RELEVANT_SHEETS[1]+'!'+RELEVANT_RANGE).execute().get('values', [])



@module.commands('helpsearch')
def helpsearch(bot, trigger):
    isAdminChannel = (trigger.sender in bot.config.logtools.admin_channels)
    if not isAdminChannel:
        return
    help_content = parser.format_help()
    help_content = help_content.replace('sopel', ',search')
    url = create_snoonet_paste(help_content)
    bot.reply(url)



def create_snoonet_paste(paste_content):
    '''Creates a ghostpaste and returns the link to it'''
    paste_url_prefix = 'https://paste.snoonet.org/paste/'
    post_url = paste_url_prefix+'new'

    data = {'lang': 'Plain Text',
            'text': paste_content,
            'expire': -1,
            'password': None,
            'title': None}

    # We create a post
    response = requests.post(post_url, data=data)

    #return paste_url_prefix + url_unique_code
    return response.url

