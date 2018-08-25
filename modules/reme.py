# coding=utf8
"""
A kit of reme-related code
"""
import datetime
import pickle
import random
import sopel.module
from sopel.config.types import StaticSection, ListAttribute, ValidatedAttribute
from collections import Counter, defaultdict


PRIV_BIT_MASK = (sopel.module.HALFOP | sopel.module.OP | sopel.module.ADMIN | sopel.module.OWNER)


class RemeSection(StaticSection):
    '''A class containing the configuration parameters for the module.'''
    admin_channels = ListAttribute('admin_channels')
    allowed_channels = ListAttribute('allowed_channels')
    days_before_forgotten = ValidatedAttribute('days_before_forgotten', int, default=14)
    minimum_time_seconds = ValidatedAttribute('minimum_time_seconds', int, default=7200)
    minimum_line_number = ValidatedAttribute('minimum_line_number', int, default=30)
    sass_list = ListAttribute('sass_list')


def configure(config):
    '''Invoked upon parameter configuration mode'''
    config.define_section('reme', RemeSection, validate=True)
    config.reme.configure_setting('days_before_forgotten', 'Days before user is forgotten')
    config.reme.configure_setting('minimum_time_seconds', 'Minimum join time in seconds')
    config.reme.configure_setting('minimum_line_number', 'minimum number of chat lines')


def setup(bot):
    '''Invoked when the module is loaded.'''
    bot.config.define_section('reme', RemeSection, validate=True)
    try:
        with open('reme.pickle', 'rb') as file_handle:
            bot.memory['ops_cmd_users'] = pickle.load(file_handle)
    except FileNotFoundError:
        bot.memory['ops_cmd_users'] = dict()
    except EOFError:
        print('the reme file was corrupted, using a new one')
        bot.memory['ops_cmd_users'] = dict()


@sopel.module.interval(1200)
def save_to_file(bot):
    '''Saves the data as backup in a file'''
    with open('reme.pickle', 'wb') as file_handle:
        pickle.dump(bot.memory['ops_cmd_users'], file_handle)


@sopel.module.interval(30)
def manage_mini_users_dict(bot):
    '''Manages the users dict for this module only'''

    # keep track of current users
    users_to_add = dict()
    for channel in bot.privileges.keys():
        if channel in bot.config.reme.allowed_channels:
            for user in bot.privileges[channel]:
                if user not in bot.memory['ops_cmd_users']:
                    # first seen, last seen, line number
                    users_to_add[user] = [datetime.datetime.now(),
                                          datetime.datetime.now(), 0]
                else:
                    bot.memory['ops_cmd_users'][user][1] = datetime.datetime.now()
    bot.memory['ops_cmd_users'].update(users_to_add)

    # purge users if they were not in the channel for two weeks
    users_to_delete = []
    for user in bot.memory['ops_cmd_users']:
        first_seen = bot.memory['ops_cmd_users'][user][0]
        last_seen = bot.memory['ops_cmd_users'][user][1]
        if (last_seen-first_seen).days > bot.config.reme.days_before_forgotten:
            users_to_delete.append(user)

    for user in users_to_delete:
        del bot.memory['ops_cmd_users'][user]


@sopel.module.rule('.*')
def increment_msg_counter(bot, message):
    '''When a user message happens, increments the counter.'''
    if message.nick in bot.memory['ops_cmd_users']:
        bot.memory['ops_cmd_users'][message.nick][2] += 1
    else:
        # The 1 is for the current message
        bot.memory['ops_cmd_users'][message.nick] = [datetime.datetime.now(),
                                                     datetime.datetime.now(), 1]


@sopel.module.rule(r"\?ops(?:\s.*|$)")
def smart_ops(bot, message):
    '''A smart version of the ops command, only if enough messages and time in the channel.'''
    if message.sender in bot.config.reme.allowed_channels:
        users = bot.privileges[message.sender]

        if message.nick in bot.memory['ops_cmd_users']:
            asker_info = bot.memory['ops_cmd_users'][message.nick]
        else:
            asker_info = [datetime.datetime.now(), datetime.datetime.now(), 1]

        minimum_time_delta = datetime.timedelta(0, bot.config.reme.minimum_time_seconds)
        is_old_enough = (asker_info[1]-asker_info[0]) > minimum_time_delta
        has_enough_lines = asker_info[2] > bot.config.reme.minimum_line_number
        is_privileged = users[message.nick] & PRIV_BIT_MASK
        if (is_old_enough and has_enough_lines) or is_privileged:
            # get relevant users to alert
            users_to_alert = list()
            for user_and_priv_lvl in users.items():
                if user_and_priv_lvl[1] & PRIV_BIT_MASK:
                    users_to_alert.append(user_and_priv_lvl[0])
            alert_string_to_say = ', '.join(users_to_alert)
            bot.say(alert_string_to_say)
        else:
            bot.say(random.choice(bot.config.reme.sass_list))


@sopel.module.commands('clones')
def multipleusers(bot, trigger):
    '''Finds users that are joined multiple times'''
    is_admin_channel = (trigger.sender in bot.config.logtools.admin_channels)
    if not is_admin_channel:
        return

    nicks_by_host = defaultdict(set)
    for a_channel in bot.config.reme.allowed_channels:
        for user_nick in bot.privileges[a_channel]:
            user_obj = bot.users[user_nick]
            user_host = user_obj.host
            is_privileged = bot.privileges[a_channel][user_nick] & PRIV_BIT_MASK
            is_network_admin = 'snoonet/' in user_host.lower()
            if is_network_admin or is_privileged:  # avoid the administrator peeps
                continue
            nicks_by_host[user_host].add(user_nick)
    multiple_users = {k: v for k, v in nicks_by_host.items() if len(v) > 1}
    bot.say(str(multiple_users)+'.', max_messages=3)


@sopel.module.commands('idlist')
def listsortedids(bot, trigger):
    '''Serves the list of users who have irccloud-style ids as user'''
    is_admin_channel = (trigger.sender in bot.config.logtools.admin_channels)
    if not is_admin_channel:
        return

    uid_set = set()
    sid_set = set()
    for a_channel in bot.config.reme.allowed_channels:
        for user_nick in bot.privileges[a_channel]:
            user_obj = bot.users[user_nick]
            user_user = user_obj.user
            if user_user[0:3] == 'uid':
                uid_set.add(user_user)
            elif user_user[0:3] == 'sid':
                sid_set.add(user_user)
    uid_list = list(int(i[3:]) for i in uid_set)
    uid_list.sort()
    uid_list = [str(i) for i in uid_list]
    sid_list = list(int(i[3:]) for i in sid_set)
    sid_list.sort()
    sid_list = [str(i) for i in sid_list]
    bot.say('registered: ' + ', '.join(sid_list) + '   unregistered: ' + ', '.join(uid_list) + '.', max_messages=3)

