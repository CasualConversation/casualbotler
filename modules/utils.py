#!/usr/bin/env python3

import datetime
import tempfile
import pygments
import boto3
from pygments.lexers import IrcLogsLexer
from pygments.formatters import HtmlFormatter



def get_mod_emoji(mod_nick):
    if mod_nick in  ('A_D, A_Dragon'):
        center = '\U0001F432'
    elif mod_nick == 'anders':
        center = '\U0001F34D'
    elif mod_nick == 'carawayseeds':
        center = '\U0001F335'
    elif mod_nick in ('DavidLuizsHair', 'Tapu-Fini'):
        center = '\U0001F9A1'
    elif mod_nick == 'diss':
        center = '\U0001F435'
    elif mod_nick in ('entropy', 'void', 'unicorn', 'unic0rn23'):
        center = '\U0001F998'
    elif mod_nick == 'janesays':
        center = '\U0001F377'
    elif mod_nick == 'SolarFlare':
        center = '\U0001F411'
    elif mod_nick == 'LeMapleMoose':
        center = '\U0001F98C'
    elif mod_nick in ('linuxdaemon', 'pizza', 'linuxinthecloud'):
        center = '\U0001F43A'
    elif mod_nick == 'Matthew':
        center = '\U0001F48A'
    elif mod_nick == 'owlet':
        center = '\U0001F989'
    elif mod_nick == 'timekeeper':
        center = '\U0001F359'
    elif mod_nick == 'znuxor':
        center = '\U0001F916'
    else:
        center = '\U0001F60E'
    return center


def create_s3_paste(s3_bucket_name, paste_content, wanted_title=None):
    '''Creates a paste and returns the link to the formatted version'''
    if wanted_title:
        file_title = wanted_title
    else:
        file_title = datetime.datetime.now().replace(microsecond=0).isoformat().replace(':', '').replace('.', '').replace('-', '')+'Z'

    filename_text = file_title + '.txt'
    filename_formatted = file_title + '.html'

    paste_content_formatted = pygments.highlight(paste_content, IrcLogsLexer(), HtmlFormatter(full=True, style='monokai'))

    filelike_text = tempfile.TemporaryFile()
    filelike_text.write(paste_content.encode('utf-8'))
    filelike_text.seek(0)

    filelike_formatted = tempfile.TemporaryFile()
    filelike_formatted.write(paste_content_formatted.encode('utf-8'))
    filelike_formatted.seek(0)

    s3client = boto3.client('s3')
    s3resource = boto3.resource('s3')
    s3client.upload_fileobj(filelike_text, s3_bucket_name, filename_text)
    s3client.upload_fileobj(filelike_formatted, s3_bucket_name, filename_formatted)

    # Set the content-type, it cannot be done at upload time it seems...
    obj_text = s3resource.Object(s3_bucket_name, filename_text)
    obj_text.copy_from(CopySource={'Bucket': s3_bucket_name, 'Key': filename_text}, MetadataDirective='REPLACE', ContentType='text/plain; charset=utf-8')
    obj_formatted = s3resource.Object(s3_bucket_name, filename_formatted)
    obj_formatted.copy_from(CopySource={'Bucket': s3_bucket_name, 'Key': filename_formatted}, MetadataDirective='REPLACE', ContentType='text/html; charset=utf-8')

    # Make the url to return
    url = 'http://{}/{}'.format(s3_bucket_name, filename_formatted)
    return url
