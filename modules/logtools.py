#!/usr/bin/env python3
'''Stuff about making the spreadsheet information a bit more useful'''

import shlex
import argparse
import time
import requests
from apiclient.discovery import build
from fuzzywuzzy import fuzz
from sopel import module
from sopel.config.types import StaticSection, ListAttribute, ValidatedAttribute


class LogToolsSection(StaticSection):
    '''Data class containing the parameters for the module.'''
    google_api_key_password = ValidatedAttribute('google_api_key_password')
    admin_channels = ListAttribute('admin_channels')
    acceptable_fuzz_ratio = ValidatedAttribute('acceptable_fuzz_ratio', int, default=75)
    spreadsheet_id = ValidatedAttribute('spreadsheet_id')
    relevant_sheets = ListAttribute('relevant_sheets')
    relevant_range = ValidatedAttribute('relevant_range')


def configure(config):
    '''Invoked by the configuration building mode of sopel.'''
    config.define_section('logtools', LogToolsSection, validate=True)


parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('term', type=str, help='the nick or piece of mask to search')


def setup(bot):
    '''Invoked when the module is loaded.'''
    bot.config.define_section('logtools', LogToolsSection, validate=True)
    bot.memory['google_sheets_service'] = \
        build('sheets', 'v4',
              developerKey=bot.config.logtools.google_api_key_password,
              cache_discovery=False)
    if 'sheet_content_1' in bot.memory:
        del bot.memory['sheet_content_1']
    if 'sheet_content_2' in bot.memory:
        del bot.memory['sheet_content_2']


ACCEPTABLE_RATIO = 75


def search_for_indexes(bot, args):
    '''Searches the data in the sheets, returns the indexes.'''
    found_indexes_1 = []
    found_indexes_2 = []

    for index, line in enumerate(bot.memory['sheet_content_1']):
        if line and (args.term in line[8] or fuzz.ratio(args.term.lower(),
                                                        line[1].lower()) >= ACCEPTABLE_RATIO):
            found_indexes_1.append(index)

    for index, line in enumerate(bot.memory['sheet_content_2']):
        if line and (args.term in line[8] or fuzz.ratio(args.term.lower(),
                                                        line[1].lower()) >= ACCEPTABLE_RATIO):
            found_indexes_2.append(index)
    return found_indexes_1, found_indexes_2


@module.commands('search')
def search(bot, trigger):
    '''Searches for a nick (fuzzy) for a part of a netmask in the spreadsheets.'''

    is_admin_channel = (trigger.sender in bot.config.logtools.admin_channels)
    if not is_admin_channel:
        return

    arguments = trigger.groups()[1]
    if arguments is None:
        bot.reply('No arguments :(   To learn the command syntax, please use -h')
        return
    try:
        args = parser.parse_args(shlex.split(arguments))
    except SystemExit:
        if '-h' in arguments or '--help' in arguments:
            helpsearch(bot, trigger)
        else:
            bot.reply('invalid arguments :(   To learn the command syntax, please use -h')
        return

    if 'sheet_content_1' not in bot.memory:
        refresh_spreadsheet_content(bot)

    found_indexes_1, found_indexes_2 = search_for_indexes(bot, args)

    sheet_1_instances = []
    sheet_2_instances = []
    for an_index in found_indexes_1:
        relevant_row = bot.memory['sheet_content_1'][an_index]
        report_str = '{} on {} ({}) (row {})'.format(relevant_row[2],
                                                     relevant_row[1],
                                                     relevant_row[8],
                                                     an_index+1+1)  # for index and missing row
        sheet_1_instances.append(report_str)
    for an_index in found_indexes_2:
        relevant_row = bot.memory['sheet_content_2'][an_index]
        report_str = '{} on {} ({}) (row {} (old sheet))'.format(relevant_row[2],
                                                                 relevant_row[1],
                                                                 relevant_row[8],
                                                                 an_index+1+1)
        sheet_2_instances.append(report_str)

    instances = sheet_1_instances + sheet_2_instances

    if len(instances) > 3:
        bot.reply('\U0001F914 ' + create_snoonet_paste('\n'.join(instances)))
    else:
        bot.reply(',     '.join(instances))


RELEVANT_SHEETS = ('Operator Actions', 'Pre 2018 Data')
RELEVANT_RANGE = 'a2:k'


@module.interval(300)
def refresh_spreadsheet_content(bot):
    '''Periodically refreshes the spreadsheet content.
    This is done this way to limits calls to the API.'''

    values_obj = bot.memory['google_sheets_service'].spreadsheets().values()

    range_1 = RELEVANT_SHEETS[0]+'!'+RELEVANT_RANGE
    range_2 = RELEVANT_SHEETS[1]+'!'+RELEVANT_RANGE
    bot.memory['sheet_content_1'] = \
        values_obj.get(spreadsheetId=bot.config.logtools.spreadsheet_id,
                       range=range_1).execute().get('values', [])
    time.sleep(1)
    bot.memory['sheet_content_2'] = \
        values_obj.get(spreadsheetId=bot.config.logtools.spreadsheet_id,
                       range=range_2).execute().get('values', [])


@module.commands('helpsearch')
def helpsearch(bot, trigger):
    '''Serves the help documentation.'''
    is_admin_channel = (trigger.sender in bot.config.logtools.admin_channels)
    if not is_admin_channel:
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

    return response.url
