#!/usr/bin/env python3
'''Stuff about making the spreadsheet information a bit more useful'''

import collections
import shlex
import argparse
import sys
import os
from apiclient.discovery import build
from fuzzywuzzy import fuzz
from sopel import module
from sopel.config.types import StaticSection, ListAttribute, ValidatedAttribute

# hack for relative import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import from_admin_channel_only, create_s3_paste


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


SEARCH_CMD_PARSER = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
SEARCH_CMD_PARSER.add_argument('terms',
                               type=str,
                               nargs='+',
                               help='the nick or piece of mask to search')
SEARCH_CMD_PARSER.add_argument('-c', '--convert', action='store_true')


def setup(bot):
    '''Invoked when the module is loaded.'''
    bot.config.define_section('logtools', LogToolsSection, validate=True)
    bot.memory['google_sheets_service'] = \
        build('sheets', 'v4',
              developerKey=bot.config.logtools.google_api_key_password,
              cache_discovery=False)

    for sheet_name in bot.config.logtools.relevant_sheets:
        if sheet_name in bot.memory:
            del bot.memory[sheet_name]

ACCEPTABLE_RATIO = 75


def search_for_indexes(bot, search_term):
    '''Searches the data in the sheets, returns the indexes.'''
    found_indexes = []

    for sheet in bot.config.logtools.relevant_sheets:
        index_list = []
        for index, line in enumerate(bot.memory[sheet]):
            if line and (search_term in line[8] or fuzz.ratio(search_term.lower(),
                                                              line[1].lower()) >= ACCEPTABLE_RATIO):
                index_list.append(index)
        found_indexes.append(index_list)

    return found_indexes


@module.commands('latest')
@from_admin_channel_only
def latest(bot, trigger):
    '''Returns the latest logged items'''
    if bot.config.logtools.relevant_sheets[0] not in bot.memory:
        refresh_spreadsheet_content(bot)

    entry_number = len(bot.memory[bot.config.logtools.relevant_sheets[0]])

    sheet_1_instances = []

    for an_index in range(max(entry_number-3-1, 0), entry_number-1):
        relevant_row = bot.memory[bot.config.logtools.relevant_sheets[0]][an_index]
        if any(relevant_row):
            current_entry = create_entry_from_row(relevant_row, an_index)
            report_str = format_spreadsheet_line(current_entry, '2019')
            sheet_1_instances.append(report_str)
    for an_instance in sheet_1_instances:
        bot.say('\u25A0 ' + an_instance, max_messages=2)


LogEntry = collections.namedtuple("LogEntry", ["timestamp",
                                               "username",
                                               "result",
                                               "length",
                                               "op",
                                               "second_op",
                                               "channel",
                                               "reason",
                                               "host",
                                               "log",
                                               "additional_info",
                                               "index"])


@module.commands('search')
@from_admin_channel_only
def search(bot, trigger):
    '''Searches for a nick (fuzzy) for a part of a netmask in the spreadsheets.'''
    arguments = trigger.groups()[1]
    if arguments is None:
        bot.reply('No arguments :(   To learn the command syntax, please use -h')
        return
    try:
        args = SEARCH_CMD_PARSER.parse_args(shlex.split(arguments))
    except SystemExit:
        if '-h' in arguments or '--help' in arguments:
            helpsearch(bot, trigger)
        else:
            bot.reply('invalid arguments :(   To learn the command syntax, please use -h')
        return

    if args.convert:
        search_terms = []
        for a_nick in args.terms:
            lowercase_users = dict()
            for user in bot.users:
                lowercase_users[user.lower()] = bot.users[user].host
            if a_nick.lower() in lowercase_users:
                host_term = lowercase_users[a_nick.lower()]
                search_terms.append(host_term)
                bot.say('Converted {} to {}, using it for the search...'.format(a_nick, host_term))
            else:
                search_terms.append(a_nick)
                warn_msg = 'Could not convert {} to a host (not in channels). '.format(a_nick) + \
                           'Searching without conversion...'
                bot.say(warn_msg)
    else:
        search_terms = args.terms

    if bot.config.logtools.relevant_sheets[0] not in bot.memory:
        refresh_spreadsheet_content(bot)

    indexes_by_sheet = []
    for _ in bot.config.logtools.relevant_sheets:
        indexes_by_sheet.append([])

    for a_term in search_terms:
        if a_term is None:
            continue
        term_indexes_by_sheet= search_for_indexes(bot, a_term)
        for index, content in enumerate(term_indexes_by_sheet):
            indexes_by_sheet[index].extend(content)

    for i in range(len(indexes_by_sheet)):
        indexes_by_sheet[i] = list(sorted(set(indexes_by_sheet[i])))


    instances_per_sheet = []
    for i, sheet in enumerate(bot.config.logtools.relevant_sheets):
        sheet_found_indexes = indexes_by_sheet[i]
        curr_sheet_instances = []
        for match_index in sheet_found_indexes:
            relevant_row = bot.memory[sheet][match_index]
            current_entry = create_entry_from_row(relevant_row, match_index)
            report_str = format_spreadsheet_line(current_entry, sheet)
            curr_sheet_instances.append(report_str)
        instances_per_sheet.append(curr_sheet_instances)


    instances = []
    for instance_list in instances_per_sheet:
        instances.extend(instance_list)

    if len(instances) > 3:
        answer = '\U0001F914 ' + create_s3_paste(bot.config.banlogger.s3_bucket_name,
                                                 '\n'.join(instances))
        bot.say(answer, max_messages=3)
    elif not instances:
        bot.say('None found.')
    else:
        for an_instance in instances:
            answer = '\u25A0 ' + an_instance
            bot.say(answer, max_messages=2)


def create_entry_from_row(spreadsheet_row, row_index):
    '''Creates a namedtuple for a row as an indirection layer.'''
    log_entry = LogEntry(timestamp=spreadsheet_row[0],
                         username=spreadsheet_row[1],
                         result=spreadsheet_row[2],
                         length=spreadsheet_row[3],
                         op=spreadsheet_row[4],
                         second_op=spreadsheet_row[5],
                         channel=spreadsheet_row[6],
                         reason=spreadsheet_row[7],
                         host=spreadsheet_row[8],
                         log=spreadsheet_row[9],
                         additional_info=spreadsheet_row[10],
                         index=row_index+2)  # 0-index to 1-index, and top row
    return log_entry


def format_spreadsheet_line(entry, sheet_name):
    '''Returns a formatted spreadsheet line for report.'''
    report_str = '{} on {} ({}) '.format(entry.result, entry.username, entry.host)
    if entry.length:
        report_str += '(duration: {}) '.format(entry.length)
    report_str += 'in channel {} on {} because "{}" (see {}) (row {}) ({})'.format(entry.channel,
                                                                                   entry.timestamp,
                                                                                   entry.reason,
                                                                                   entry.log,
                                                                                   entry.index,
                                                                                   sheet_name)
    return report_str


RELEVANT_RANGE = 'a2:l'


@module.interval(60)
def refresh_spreadsheet_content(bot):
    '''Periodically refreshes the spreadsheet content.
    This is done this way to limits calls to the API.'''

    values_obj = bot.memory['google_sheets_service'].spreadsheets().values()
    spreadsheetId = bot.config.logtools.spreadsheet_id

    for sheet in bot.config.logtools.relevant_sheets:
        curr_range = sheet+'!'+RELEVANT_RANGE
        bot.memory[sheet] = values_obj.get(spreadsheetId=spreadsheetId,
                                           range=curr_range).execute().get('values', [])


@module.commands('helpsearch')
@from_admin_channel_only
def helpsearch(bot, trigger):
    '''Serves the help documentation.'''
    help_content = SEARCH_CMD_PARSER.format_help()
    help_content = help_content.replace('sopel', ',search')
    url = create_s3_paste(bot.config.banlogger.s3_bucket_name,
                          help_content,
                          wanted_title="searchcommandhelp")
    bot.reply(url)
