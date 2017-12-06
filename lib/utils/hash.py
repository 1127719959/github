#!/usr/bin/env python
#coding=utf-8
"""
Copyright (c) 2006-2017 sqlmap developers (http://sqlmap.org/)
See the file 'doc/COPYING' for copying permission
"""

try:
    from crypt import crypt
except ImportError:
    from thirdparty.fcrypt.fcrypt import crypt

_multiprocessing = None
try:
    import multiprocessing

    # problems on FreeBSD (Reference: http://www.eggheadcafe.com/microsoft/Python/35880259/multiprocessing-on-freebsd.aspx)
    _ = multiprocessing.Queue()
except (ImportError, OSError):
    pass
else:
    try:
        if multiprocessing.cpu_count() > 1:
            _multiprocessing = multiprocessing
    except NotImplementedError:
        pass

import gc
import os
import re
import tempfile
import time
import zipfile

from hashlib import md5
from hashlib import sha1
from hashlib import sha224
from hashlib import sha384
from hashlib import sha512
from Queue import Queue

from lib.core.common import Backend
from lib.core.common import checkFile
from lib.core.common import clearConsoleLine
from lib.core.common import dataToStdout
from lib.core.common import getFileItems
from lib.core.common import getPublicTypeMembers
from lib.core.common import getSafeExString
from lib.core.common import getUnicode
from lib.core.common import hashDBRetrieve
from lib.core.common import hashDBWrite
from lib.core.common import normalizeUnicode
from lib.core.common import paths
from lib.core.common import readInput
from lib.core.common import singleTimeLogMessage
from lib.core.common import singleTimeWarnMessage
from lib.core.convert import hexdecode
from lib.core.convert import hexencode
from lib.core.convert import utf8encode
from lib.core.data import conf
from lib.core.data import kb
from lib.core.data import logger
from lib.core.enums import DBMS
from lib.core.enums import HASH
from lib.core.enums import MKSTEMP_PREFIX
from lib.core.exception import SqlmapDataException
from lib.core.exception import SqlmapUserQuitException
from lib.core.settings import COMMON_PASSWORD_SUFFIXES
from lib.core.settings import COMMON_USER_COLUMNS
from lib.core.settings import DUMMY_USER_PREFIX
from lib.core.settings import HASH_MOD_ITEM_DISPLAY
from lib.core.settings import HASH_RECOGNITION_QUIT_THRESHOLD
from lib.core.settings import IS_WIN
from lib.core.settings import ITOA64
from lib.core.settings import NULL
from lib.core.settings import UNICODE_ENCODING
from lib.core.settings import ROTATING_CHARS
from lib.core.wordlist import Wordlist
from thirdparty.colorama.initialise import init as coloramainit
from thirdparty.pydes.pyDes import des
from thirdparty.pydes.pyDes import CBC

def mysql_passwd(password, uppercase=True):
    """
    Reference(s):
        http://csl.sublevel3.org/mysql-password-function/

    >>> mysql_passwd(password='testpass', uppercase=True)
    '*00E247AC5F9AF26AE0194B41E1E769DEE1429A29'
    """

    retVal = "*%s" % sha1(sha1(password).digest()).hexdigest()

    return retVal.upper() if uppercase else retVal.lower()

def mysql_old_passwd(password, uppercase=True):  # prior to version '4.1'
    """
    Reference(s):
        http://www.sfr-fresh.com/unix/privat/tpop3d-1.5.5.tar.gz:a/tpop3d-1.5.5/password.c
        http://voidnetwork.org/5ynL0rd/darkc0de/python_script/darkMySQLi.html

    >>> mysql_old_passwd(password='testpass', uppercase=True)
    '7DCDA0D57290B453'
    """

    a, b, c = 1345345333, 7, 0x12345671

    for d in password:
        if d == ' ' or d == '\t':
            continue

        e = ord(d)
        a ^= (((a & 63) + b) * e) + (a << 8)
        c += (c << 8) ^ a
        b += e

    retVal = "%08lx%08lx" % (a & ((1 << 31) - 1), c & ((1 << 31) - 1))

    return retVal.upper() if uppercase else retVal.lower()

def postgres_passwd(password, username, uppercase=False):
    """
    Reference(s):
        http://pentestmonkey.net/blog/cracking-postgres-hashes/

    >>> postgres_passwd(password='testpass', username='testuser', uppercase=False)
    'md599e5ea7a6f7c3269995cba3927fd0093'
    """


    if isinstance(username, unicode):
        username = unicode.encode(username, UNICODE_ENCODING)

    if isinstance(password, unicode):
        password = unicode.encode(password, UNICODE_ENCODING)

    retVal = "md5%s" % md5(password + username).hexdigest()

    return retVal.upper() if uppercase else retVal.lower()

def mssql_passwd(password, salt, uppercase=False):
    """
    Reference(s):
        http://www.leidecker.info/projects/phrasendrescher/mssql.c
        https://www.evilfingers.com/tools/GSAuditor.php

    >>> mssql_passwd(password='testpass', salt='4086ceb6', uppercase=False)
    '0x01004086ceb60c90646a8ab9889fe3ed8e5c150b5460ece8425a'
    """

    binsalt = hexdecode(salt)
    unistr = "".join(map(lambda c: ("%s\0" if ord(c) < 256 else "%s") % utf8encode(c), password))

    retVal = "0100%s%s" % (salt, sha1(unistr + binsalt).hexdigest())

    return "0x%s" % (retVal.upper() if uppercase else retVal.lower())

def mssql_old_passwd(password, salt, uppercase=True):  # prior to version '2005'
    """
    Reference(s):
        www.exploit-db.com/download_pdf/15537/
        http://www.leidecker.info/projects/phrasendrescher/mssql.c
        https://www.evilfingers.com/tools/GSAuditor.php

    >>> mssql_old_passwd(password='testpass', salt='4086ceb6', uppercase=True)
    '0x01004086CEB60C90646A8AB9889FE3ED8E5C150B5460ECE8425AC7BB7255C0C81D79AA5D0E93D4BB077FB9A51DA0'
    """

    binsalt = hexdecode(salt)
    unistr = "".join(map(lambda c: ("%s\0" if ord(c) < 256 else "%s") % utf8encode(c), password))

    retVal = "0100%s%s%s" % (salt, sha1(unistr + binsalt).hexdigest(), sha1(unistr.upper() + binsalt).hexdigest())

    return "0x%s" % (retVal.upper() if uppercase else retVal.lower())

def mssql_new_passwd(password, salt, uppercase=False):
    """
    Reference(s):
        http://hashcat.net/forum/thread-1474.html

    >>> mssql_new_passwd(password='testpass', salt='4086ceb6', uppercase=False)
    '0x02004086ceb6eb051cdbc5bdae68ffc66c918d4977e592f6bdfc2b444a7214f71fa31c35902c5b7ae773ed5f4c50676d329120ace32ee6bc81c24f70711eb0fc6400e85ebf25'
    """

    binsalt = hexdecode(salt)
    unistr = "".join(map(lambda c: ("%s\0" if ord(c) < 256 else "%s") % utf8encode(c), password))

    retVal = "0200%s%s" % (salt, sha512(unistr + binsalt).hexdigest())

    return "0x%s" % (retVal.upper() if uppercase else retVal.lower())

def oracle_passwd(password, salt, uppercase=True):
    """
    Reference(s):
        https://www.evilfingers.com/tools/GSAuditor.php
        http://www.notesbit.com/index.php/scripts-oracle/oracle-11g-new-password-algorithm-is-revealed-by-seclistsorg/
        http://seclists.org/bugtraq/2007/Sep/304

    >>> oracle_passwd(password='SHAlala', salt='1B7B5F82B7235E9E182C', uppercase=True)
    'S:2BFCFDF5895014EE9BB2B9BA067B01E0389BB5711B7B5F82B7235E9E182C'
    """

    binsalt = hexdecode(salt)

    retVal = "s:%s%s" % (sha1(utf8encode(password) + binsalt).hexdigest(), salt)

    return retVal.upper() if uppercase else retVal.lower()

def oracle_old_passwd(password, username, uppercase=True):  # prior to version '11g'
    """
    Reference(s):
        http://www.notesbit.com/index.php/scripts-oracle/oracle-11g-new-password-algorithm-is-revealed-by-seclistsorg/

    >>> oracle_old_passwd(password='tiger', username='scott', uppercase=True)
    'F894844C34402B67'
    """

    IV, pad = "\0" * 8, "\0"

    if isinstance(username, unicode):
        username = unicode.encode(username, UNICODE_ENCODING)

    if isinstance(password, unicode):
        password = unicode.encode(password, UNICODE_ENCODING)

    unistr = "".join("\0%s" % c for c in (username + password).upper())

    cipher = des(hexdecode("0123456789ABCDEF"), CBC, IV, pad)
    encrypted = cipher.encrypt(unistr)
    cipher = des(encrypted[-8:], CBC, IV, pad)
    encrypted = cipher.encrypt(unistr)

    retVal = hexencode(encrypted[-8:])

    return retVal.upper() if uppercase else retVal.lower()

def md5_generic_passwd(password, uppercase=False):
    """
    >>> md5_generic_passwd(password='testpass', uppercase=False)
    '179ad45c6ce2cb97cf1029e212046e81'
    """

    retVal = md5(password).hexdigest()

    return retVal.upper() if uppercase else retVal.lower()

def sha1_generic_passwd(password, uppercase=False):
    """
    >>> sha1_generic_passwd(password='testpass', uppercase=False)
    '206c80413b9a96c1312cc346b7d2517b84463edd'
    """

    retVal = sha1(password).hexdigest()

    return retVal.upper() if uppercase else retVal.lower()

def sha224_generic_passwd(password, uppercase=False):
    """
    >>> sha224_generic_passwd(password='testpass', uppercase=False)
    '648db6019764b598f75ab6b7616d2e82563a00eb1531680e19ac4c6f'
    """

    retVal = sha224(password).hexdigest()

    return retVal.upper() if uppercase else retVal.lower()

def sha384_generic_passwd(password, uppercase=False):
    """
    >>> sha384_generic_passwd(password='testpass', uppercase=False)
    '6823546e56adf46849343be991d4b1be9b432e42ed1b4bb90635a0e4b930e49b9ca007bc3e04bf0a4e0df6f1f82769bf'
    """

    retVal = sha384(password).hexdigest()

    return retVal.upper() if uppercase else retVal.lower()

def sha512_generic_passwd(password, uppercase=False):
    """
    >>> sha512_generic_passwd(password='testpass', uppercase=False)
    '78ddc8555bb1677ff5af75ba5fc02cb30bb592b0610277ae15055e189b77fe3fda496e5027a3d99ec85d54941adee1cc174b50438fdc21d82d0a79f85b58cf44'
    """

    retVal = sha512(password).hexdigest()

    return retVal.upper() if uppercase else retVal.lower()

def crypt_generic_passwd(password, salt, uppercase=False):
    """
    Reference(s):
        http://docs.python.org/library/crypt.html
        http://helpful.knobs-dials.com/index.php/Hashing_notes
        http://php.net/manual/en/function.crypt.php
        http://carey.geek.nz/code/python-fcrypt/

    >>> crypt_generic_passwd(password='rasmuslerdorf', salt='rl', uppercase=False)
    'rl.3StKT.4T8M'
    """

    retVal = crypt(password, salt)

    return retVal.upper() if uppercase else retVal

def wordpress_passwd(password, salt, count, prefix, uppercase=False):
    """
    Reference(s):
        http://packetstormsecurity.org/files/74448/phpassbrute.py.txt
        http://scriptserver.mainframe8.com/wordpress_password_hasher.php

    >>> wordpress_passwd(password='testpass', salt='aD9ZLmkp', count=2048, prefix='$P$9aD9ZLmkp', uppercase=False)
    '$P$9aD9ZLmkpsN4A83G8MefaaP888gVKX0'
    """

    def _encode64(input_, count):
        output = ''
        i = 0

        while i < count:
            value = ord(input_[i])
            i += 1
            output = output + ITOA64[value & 0x3f]

            if i < count:
                value = value | (ord(input_[i]) << 8)

            output = output + ITOA64[(value >> 6) & 0x3f]

            i += 1
            if i >= count:
                break

            if i < count:
                value = value | (ord(input_[i]) << 16)

            output = output + ITOA64[(value >> 12) & 0x3f]

            i += 1
            if i >= count:
                break

            output = output + ITOA64[(value >> 18) & 0x3f]

        return output

    if isinstance(password, unicode):
        password = password.encode(UNICODE_ENCODING)

    cipher = md5(salt)
    cipher.update(password)
    hash_ = cipher.digest()

    for i in xrange(count):
        _ = md5(hash_)
        _.update(password)
        hash_ = _.digest()

    retVal = prefix + _encode64(hash_, 16)

    return retVal.upper() if uppercase else retVal

__functions__ = {
                    HASH.MYSQL: mysql_passwd,
                    HASH.MYSQL_OLD: mysql_old_passwd,
                    HASH.POSTGRES: postgres_passwd,
                    HASH.MSSQL: mssql_passwd,
                    HASH.MSSQL_OLD: mssql_old_passwd,
                    HASH.MSSQL_NEW: mssql_new_passwd,
                    HASH.ORACLE: oracle_passwd,
                    HASH.ORACLE_OLD: oracle_old_passwd,
                    HASH.MD5_GENERIC: md5_generic_passwd,
                    HASH.SHA1_GENERIC: sha1_generic_passwd,
                    HASH.SHA224_GENERIC: sha224_generic_passwd,
                    HASH.SHA384_GENERIC: sha384_generic_passwd,
                    HASH.SHA512_GENERIC: sha512_generic_passwd,
                    HASH.CRYPT_GENERIC: crypt_generic_passwd,
                    HASH.WORDPRESS: wordpress_passwd,
                }

def storeHashesToFile(attack_dict):
    if not attack_dict:
        return

    if kb.storeHashesChoice is None:
        message = u"您是否要将哈希值存储到临时文件中，"
        message += u"以便最终使用其他工具进一步处理[y/N] "

        kb.storeHashesChoice = readInput(message, default='N', boolean=True)

    if not kb.storeHashesChoice:
        return

    handle, filename = tempfile.mkstemp(prefix=MKSTEMP_PREFIX.HASHES, suffix=".txt")
    os.close(handle)

    infoMsg = "将哈希写入临时文件'%s' " % filename
    logger.info(infoMsg)

    items = set()

    with open(filename, "w+") as f:
        for user, hashes in attack_dict.items():
            for hash_ in hashes:
                hash_ = hash_.split()[0] if hash_ and hash_.strip() else hash_
                if hash_ and hash_ != NULL and hashRecognition(hash_):
                    item = None
                    if user and not user.startswith(DUMMY_USER_PREFIX):
                        item = "%s:%s\n" % (user.encode(UNICODE_ENCODING), hash_.encode(UNICODE_ENCODING))
                    else:
                        item = "%s\n" % hash_.encode(UNICODE_ENCODING)

                    if item and item not in items:
                        f.write(item)
                        items.add(item)

def attackCachedUsersPasswords():
    if kb.data.cachedUsersPasswords:
        results = dictionaryAttack(kb.data.cachedUsersPasswords)

        lut = {}
        for (_, hash_, password) in results:
            lut[hash_.lower()] = password

        for user in kb.data.cachedUsersPasswords.keys():
            for i in xrange(len(kb.data.cachedUsersPasswords[user])):
                if (kb.data.cachedUsersPasswords[user][i] or "").strip():
                    value = kb.data.cachedUsersPasswords[user][i].lower().split()[0]
                    if value in lut:
                        kb.data.cachedUsersPasswords[user][i] += u"%s    明文密码: %s" % ('\n' if kb.data.cachedUsersPasswords[user][i][-1] != '\n' else '', lut[value])

def attackDumpedTable():
    if kb.data.dumpedTable:
        table = kb.data.dumpedTable
        columns = table.keys()
        count = table["__infos__"]["count"]

        if not count:
            return

        infoMsg = u"转储的表中分析可能存在密码散列值"
        logger.info(infoMsg)

        found = False
        col_user = ''
        col_passwords = set()
        attack_dict = {}
        """
        包含用户名的通用列名（在某些情况下用于哈希破解）
        COMMON_USER_COLUMNS = ("login", "user", "username", "user_name", "user_login", "benutzername", "benutzer", "utilisateur", "usager", "consommateur", "utente", "utilizzatore", "usufrutuario", "korisnik", "usuario", "consumidor", "client", "cuser")
        """
        for column in columns:
            if column and column.lower() in COMMON_USER_COLUMNS:
                col_user = column
                break

        for i in xrange(count):
            # 如果在第一个给定的行数中找不到任何数据, 则放弃哈希识别
            # HASH_RECOGNITION_QUIT_THRESHOLD = 10000
            if not found and i > HASH_RECOGNITION_QUIT_THRESHOLD:
                break

            for column in columns:
                if column == col_user or column == '__infos__':
                    continue

                if len(table[column]['values']) <= i:
                    continue

                value = table[column]['values'][i]

                if hashRecognition(value):
                    found = True

                    if col_user and i < len(table[col_user]['values']):
                        if table[col_user]['values'][i] not in attack_dict:
                            attack_dict[table[col_user]['values'][i]] = []

                        attack_dict[table[col_user]['values'][i]].append(value)
                    else:
                        attack_dict['%s%d' % (DUMMY_USER_PREFIX, i)] = [value]

                    col_passwords.add(column)

        if attack_dict:
            infoMsg = u"在'%s'列中发现可能经过加密的密码散列 " % ("s" if len(col_passwords) > 1 else "")
            infoMsg += "'%s'" % ", ".join(col for col in col_passwords)
            logger.info(infoMsg)

            storeHashesToFile(attack_dict)

            message = u"你想通过基于字典的攻击来破解他们吗? %s" % ("[y/N/q]" if conf.multipleTargets else "[Y/n/q]")
            choice = readInput(message, default='N' if conf.multipleTargets else 'Y').upper()

            if choice == 'N':
                return
            elif choice == 'Q':
                raise SqlmapUserQuitException

            results = dictionaryAttack(attack_dict)
            lut = dict()

            for (_, hash_, password) in results:
                if hash_:
                    lut[hash_.lower()] = password

            infoMsg = u"保存处理后的表"
            logger.info(infoMsg)

            for i in xrange(count):
                for column in columns:
                    if not (column == col_user or column == '__infos__' or len(table[column]['values']) <= i):
                        value = table[column]['values'][i]

                        if value and value.lower() in lut:
                            table[column]['values'][i] = "%s (%s)" % (getUnicode(table[column]['values'][i]), getUnicode(lut[value.lower()]))
                            table[column]['length'] = max(table[column]['length'], len(table[column]['values'][i]))

def hashRecognition(value):
    retVal = None

    isOracle, isMySQL = Backend.isDbms(DBMS.ORACLE), Backend.isDbms(DBMS.MYSQL)

    if isinstance(value, basestring):
        for name, regex in getPublicTypeMembers(HASH):
            # Hashes for Oracle and old MySQL look the same hence these checks
            if isOracle and regex == HASH.MYSQL_OLD:
                continue
            elif isMySQL and regex == HASH.ORACLE_OLD:
                continue
            elif regex == HASH.CRYPT_GENERIC:
                if any((value.lower() == value, value.upper() == value)):
                    continue
            elif re.match(regex, value):
                retVal = regex
                break

    return retVal

def _bruteProcessVariantA(attack_info, hash_regex, suffix, retVal, proc_id, proc_count, wordlists, custom_wordlist, api):
    if IS_WIN:
        coloramainit()

    count = 0
    rotator = 0
    hashes = set([item[0][1] for item in attack_info])

    wordlist = Wordlist(wordlists, proc_id, getattr(proc_count, "value", 0), custom_wordlist)

    try:
        for word in wordlist:
            if not attack_info:
                break

            if not isinstance(word, basestring):
                continue

            if suffix:
                word = word + suffix

            try:
                current = __functions__[hash_regex](password=word, uppercase=False)

                count += 1

                if current in hashes:
                    for item in attack_info[:]:
                        ((user, hash_), _) = item

                        if hash_ == current:
                            retVal.put((user, hash_, word))

                            clearConsoleLine()

                            infoMsg = u"\r[%s] [INFO] 破解密码'%s'" % (time.strftime("%X"), word)

                            if user and not user.startswith(DUMMY_USER_PREFIX):
                                infoMsg += u" 对于用户'%s'\n" % user
                            else:
                                infoMsg += u" 对于哈希 '%s'\n" % hash_

                            dataToStdout(infoMsg, True)

                            attack_info.remove(item)

                elif (proc_id == 0 or getattr(proc_count, "value", 0) == 1) and count % HASH_MOD_ITEM_DISPLAY == 0 or hash_regex == HASH.ORACLE_OLD or hash_regex == HASH.CRYPT_GENERIC and IS_WIN:
                    rotator += 1

                    if rotator >= len(ROTATING_CHARS):
                        rotator = 0

                    status = u'当前状态: %s... %s' % (word.ljust(5)[:5], ROTATING_CHARS[rotator])

                    if not api:
                        dataToStdout("\r[%s] [INFO] %s" % (time.strftime("%X"), status))

            except KeyboardInterrupt:
                raise

            except (UnicodeEncodeError, UnicodeDecodeError):
                pass  # 忽略自定义词典中由某些单词引起的可能的编码问题

            except Exception, e:
                warnMsg = u"哈希输入时出现问题: %s (%s). " % (repr(word), e)
                warnMsg += u"请通过电子邮件报告'dev@sqlmap.org'"
                logger.critical(warnMsg)

    except KeyboardInterrupt:
        pass

    finally:
        if hasattr(proc_count, "value"):
            with proc_count.get_lock():
                proc_count.value -= 1

def _bruteProcessVariantB(user, hash_, kwargs, hash_regex, suffix, retVal, found, proc_id, proc_count, wordlists, custom_wordlist, api):
    if IS_WIN:
        coloramainit()

    count = 0
    rotator = 0

    wordlist = Wordlist(wordlists, proc_id, getattr(proc_count, "value", 0), custom_wordlist)

    try:
        for word in wordlist:
            if found.value:
                break

            current = __functions__[hash_regex](password=word, uppercase=False, **kwargs)
            count += 1

            if not isinstance(word, basestring):
                continue

            if suffix:
                word = word + suffix

            try:
                if hash_ == current:
                    if hash_regex == HASH.ORACLE_OLD:  # only for cosmetic purposes
                        word = word.upper()

                    retVal.put((user, hash_, word))

                    clearConsoleLine()

                    infoMsg = u"\r[%s] [INFO] 破解密码 '%s'" % (time.strftime("%X"), word)

                    if user and not user.startswith(DUMMY_USER_PREFIX):
                        infoMsg += u" 对于用户 '%s'\n" % user
                    else:
                        infoMsg += u" 对于哈希 '%s'\n" % hash_

                    dataToStdout(infoMsg, True)

                    found.value = True

                elif (proc_id == 0 or getattr(proc_count, "value", 0) == 1) and count % HASH_MOD_ITEM_DISPLAY == 0:
                    rotator += 1
                    if rotator >= len(ROTATING_CHARS):
                        rotator = 0
                    status = u'当前状态: %s... %s' % (word.ljust(5)[:5], ROTATING_CHARS[rotator])

                    if user and not user.startswith(DUMMY_USER_PREFIX):
                        status += ' (user: %s)' % user

                    if not api:
                        dataToStdout("\r[%s] [INFO] %s" % (time.strftime("%X"), status))

            except KeyboardInterrupt:
                raise

            except (UnicodeEncodeError, UnicodeDecodeError):
                pass  # ignore possible encoding problems caused by some words in custom dictionaries

            except Exception, e:
                warnMsg = u"哈希输入时出现问题:: %s (%s). " % (repr(word), e)
                warnMsg += u"请通过电子邮件报告'dev@sqlmap.org'"
                logger.critical(warnMsg)

    except KeyboardInterrupt:
        pass

    finally:
        if hasattr(proc_count, "value"):
            with proc_count.get_lock():
                proc_count.value -= 1

def dictionaryAttack(attack_dict):
    suffix_list = [""]
    custom_wordlist = [""]
    hash_regexes = []
    results = []
    resumes = []
    user_hash = []
    processException = False
    foundHash = False

    for (_, hashes) in attack_dict.items():
        for hash_ in hashes:
            if not hash_:
                continue

            hash_ = hash_.split()[0] if hash_ and hash_.strip() else hash_
            regex = hashRecognition(hash_)

            if regex and regex not in hash_regexes:
                hash_regexes.append(regex)
                infoMsg = u"使用哈希hash方法 '%s'" % __functions__[regex].func_name
                logger.info(infoMsg)

    for hash_regex in hash_regexes:
        keys = set()
        attack_info = []

        for (user, hashes) in attack_dict.items():
            for hash_ in hashes:
                if not hash_:
                    continue

                foundHash = True
                hash_ = hash_.split()[0] if hash_ and hash_.strip() else hash_

                if re.match(hash_regex, hash_):
                    item = None

                    if hash_regex not in (HASH.CRYPT_GENERIC, HASH.WORDPRESS):
                        hash_ = hash_.lower()

                    if hash_regex in (HASH.MYSQL, HASH.MYSQL_OLD, HASH.MD5_GENERIC, HASH.SHA1_GENERIC):
                        item = [(user, hash_), {}]
                    elif hash_regex in (HASH.ORACLE_OLD, HASH.POSTGRES):
                        item = [(user, hash_), {'username': user}]
                    elif hash_regex in (HASH.ORACLE,):
                        item = [(user, hash_), {'salt': hash_[-20:]}]
                    elif hash_regex in (HASH.MSSQL, HASH.MSSQL_OLD, HASH.MSSQL_NEW):
                        item = [(user, hash_), {'salt': hash_[6:14]}]
                    elif hash_regex in (HASH.CRYPT_GENERIC,):
                        item = [(user, hash_), {'salt': hash_[0:2]}]
                    elif hash_regex in (HASH.WORDPRESS,):
                        if ITOA64.index(hash_[3]) < 32:
                            item = [(user, hash_), {'salt': hash_[4:12], 'count': 1 << ITOA64.index(hash_[3]), 'prefix': hash_[:12]}]
                        else:
                            warnMsg = "invalid hash '%s'" % hash_
                            logger.warn(warnMsg)

                    if item and hash_ not in keys:
                        resumed = hashDBRetrieve(hash_)
                        if not resumed:
                            attack_info.append(item)
                            user_hash.append(item[0])
                        else:
                            infoMsg = u"正在恢复哈希'%s'的密码'%s'" % (hash_, resumed)
                            if user and not user.startswith(DUMMY_USER_PREFIX):
                                infoMsg += u"，对于用户'%s'" % user
                            logger.info(infoMsg)
                            resumes.append((user, hash_, resumed))
                        keys.add(hash_)

        if not attack_info:
            continue

        if not kb.wordlists:
            while not kb.wordlists:

                # the slowest of all methods hence smaller default dict
                if hash_regex in (HASH.ORACLE_OLD, HASH.WORDPRESS):
                    dictPaths = [paths.SMALL_DICT]
                else:
                    dictPaths = [paths.WORDLIST]

                message = u"你想要使用什么字典？?\n"
                message += u"[1] 默认字典文件 '%s' (按回车)\n" % dictPaths[0]
                message += u"[2] 自定义字典文件\n"
                message += u"[3] 文件与字典文件列表"
                choice = readInput(message, default='1')

                try:
                    if choice == '2':
                        message = u"自定义字典位置?\n"
                        _ = readInput(message)
                        if _:
                            dictPaths = [readInput(message)]
                            logger.info(u"使用自定义字典")
                    elif choice == '3':
                        message = u"列表文件位置?\n"
                        listPath = readInput(message)
                        checkFile(listPath)
                        dictPaths = getFileItems(listPath)
                        logger.info(u"使用自定义的字典列表")
                    else:
                        logger.info(u"使用默认字典")

                    dictPaths = filter(None, dictPaths)

                    for dictPath in dictPaths:
                        checkFile(dictPath)

                        if os.path.splitext(dictPath)[1].lower() == ".zip":
                            _ = zipfile.ZipFile(dictPath, 'r')
                            if len(_.namelist()) == 0:
                                errMsg = u"'%s'内没有文件" % dictPath
                                raise SqlmapDataException(errMsg)
                            else:
                                _.open(_.namelist()[0])

                    kb.wordlists = dictPaths

                except Exception, ex:
                    warnMsg = u"加载字典('%s')时出现问题" % getSafeExString(ex)
                    logger.critical(warnMsg)

            message = u"你想使用常用的密码后缀吗? (slow!) [y/N] "

            if readInput(message, default='N', boolean=True):
                suffix_list += COMMON_PASSWORD_SUFFIXES

        infoMsg = u"开始基于字典的破解(%s)" % __functions__[hash_regex].func_name
        logger.info(infoMsg)

        for item in attack_info:
            ((user, _), _) = item
            if user and not user.startswith(DUMMY_USER_PREFIX):
                custom_wordlist.append(normalizeUnicode(user))

        if hash_regex in (HASH.MYSQL, HASH.MYSQL_OLD, HASH.MD5_GENERIC, HASH.SHA1_GENERIC):
            for suffix in suffix_list:
                if not attack_info or processException:
                    break

                if suffix:
                    clearConsoleLine()
                    infoMsg = u"使用后缀 '%s'" % suffix
                    logger.info(infoMsg)

                retVal = None
                processes = []

                try:
                    if _multiprocessing:
                        if _multiprocessing.cpu_count() > 1:
                            infoMsg = u"启动%d个进程" % _multiprocessing.cpu_count()
                            singleTimeLogMessage(infoMsg)

                        gc.disable()

                        retVal = _multiprocessing.Queue()
                        count = _multiprocessing.Value('i', _multiprocessing.cpu_count())

                        for i in xrange(_multiprocessing.cpu_count()):
                            process = _multiprocessing.Process(target=_bruteProcessVariantA, args=(attack_info, hash_regex, suffix, retVal, i, count, kb.wordlists, custom_wordlist, conf.api))
                            processes.append(process)

                        for process in processes:
                            process.daemon = True
                            process.start()

                        while count.value > 0:
                            time.sleep(0.5)

                    else:
                        warnMsg = u"此平台目前不支持多进程哈希破解"
                        singleTimeWarnMessage(warnMsg)

                        retVal = Queue()
                        _bruteProcessVariantA(attack_info, hash_regex, suffix, retVal, 0, 1, kb.wordlists, custom_wordlist, conf.api)

                except KeyboardInterrupt:
                    print
                    processException = True
                    warnMsg = u"用户在基于字典的攻击阶段中中止(Ctrl + C被按下)"
                    logger.warn(warnMsg)

                    for process in processes:
                        try:
                            process.terminate()
                            process.join()
                        except (OSError, AttributeError):
                            pass

                finally:
                    if _multiprocessing:
                        gc.enable()

                    if retVal:
                        conf.hashDB.beginTransaction()

                        while not retVal.empty():
                            user, hash_, word = item = retVal.get(block=False)
                            attack_info = filter(lambda _: _[0][0] != user or _[0][1] != hash_, attack_info)
                            hashDBWrite(hash_, word)
                            results.append(item)

                        conf.hashDB.endTransaction()

            clearConsoleLine()

        else:
            for ((user, hash_), kwargs) in attack_info:
                if processException:
                    break

                if any(_[0] == user and _[1] == hash_ for _ in results):
                    continue

                count = 0
                found = False

                for suffix in suffix_list:
                    if found or processException:
                        break

                    if suffix:
                        clearConsoleLine()
                        infoMsg = u"使用后缀 '%s'" % suffix
                        logger.info(infoMsg)

                    retVal = None
                    processes = []

                    try:
                        if _multiprocessing:
                            if _multiprocessing.cpu_count() > 1:
                                infoMsg = u"启动%d个进程 " % _multiprocessing.cpu_count()
                                singleTimeLogMessage(infoMsg)

                            gc.disable()

                            retVal = _multiprocessing.Queue()
                            found_ = _multiprocessing.Value('i', False)
                            count = _multiprocessing.Value('i', _multiprocessing.cpu_count())

                            for i in xrange(_multiprocessing.cpu_count()):
                                process = _multiprocessing.Process(target=_bruteProcessVariantB, args=(user, hash_, kwargs, hash_regex, suffix, retVal, found_, i, count, kb.wordlists, custom_wordlist, conf.api))
                                processes.append(process)

                            for process in processes:
                                process.daemon = True
                                process.start()

                            while count.value > 0:
                                time.sleep(0.5)

                            found = found_.value != 0

                        else:
                            warnMsg = u"此平台目前不支持多进程哈希破解"
                            singleTimeWarnMessage(warnMsg)

                            class Value():
                                pass

                            retVal = Queue()
                            found_ = Value()
                            found_.value = False

                            _bruteProcessVariantB(user, hash_, kwargs, hash_regex, suffix, retVal, found_, 0, 1, kb.wordlists, custom_wordlist, conf.api)

                            found = found_.value

                    except KeyboardInterrupt:
                        print
                        processException = True
                        warnMsg = u"用户在基于字典的攻击阶段中中止(Ctrl + C被按下)"
                        logger.warn(warnMsg)

                        for process in processes:
                            try:
                                process.terminate()
                                process.join()
                            except (OSError, AttributeError):
                                pass

                    finally:
                        if _multiprocessing:
                            gc.enable()

                        if retVal:
                            conf.hashDB.beginTransaction()

                            while not retVal.empty():
                                user, hash_, word = item = retVal.get(block=False)
                                hashDBWrite(hash_, word)
                                results.append(item)

                            conf.hashDB.endTransaction()

                clearConsoleLine()

    results.extend(resumes)

    if foundHash and len(hash_regexes) == 0:
        warnMsg = u"未知的哈希格式"
        logger.warn(warnMsg)

    if len(results) == 0:
        warnMsg = u"没有找到正确的密码"
        logger.warn(warnMsg)

    return results