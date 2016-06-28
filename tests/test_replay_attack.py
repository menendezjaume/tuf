#!/usr/bin/env python

"""
<Program Name>
  test_replay_attack.py

<Author>
  Konstantin Andrianov.

<Started>
  February 22, 2012.
  
  April 5, 2014.
    Refactored to use the 'unittest' module (test conditions in code, rather
    than verifying text output), use pre-generated repository files, and
    discontinue use of the old repository tools. Expanded comments.
    -vladimir.v.diaz

<Copyright>
  See LICENSE for licensing information.

<Purpose>
  Simulate a replay, or rollback,  attack.  In a replay attack, a client is
  tricked into installing software that is older than that which the client
  previously knew to be available.

  Note: There is no difference between 'updates' and 'target' files.
"""

# Help with Python 3 compatibility, where the print statement is a function, an
# implicit relative import is invalid, and the '/' operator performs true
# division.  Example:  print 'hello world' raises a 'SyntaxError' exception.
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import os
import tempfile
import random
import time
import datetime
import shutil
import json
import subprocess
import logging
import sys

# 'unittest2' required for testing under Python < 2.7.
if sys.version_info >= (2, 7):
  import unittest

else:
  import unittest2 as unittest 

import tuf.formats
import tuf.util
import tuf.log
import tuf.client.updater as updater
import tuf.repository_tool as repo_tool
import tuf.unittest_toolbox as unittest_toolbox

import six

# The repository tool is imported and logs console messages by default.  Disable
# console log messages generated by this unit test.
repo_tool.disable_console_log_messages()

logger = logging.getLogger('tuf.test_replay_attack')



class TestReplayAttack(unittest_toolbox.Modified_TestCase):

  @classmethod
  def setUpClass(cls):
    # setUpClass() is called before any of the test cases are executed.
    
    # Create a temporary directory to store the repository, metadata, and target
    # files.  'temporary_directory' must be deleted in TearDownModule() so that
    # temporary files are always removed, even when exceptions occur. 
    cls.temporary_directory = tempfile.mkdtemp(dir=os.getcwd())
    
    # Launch a SimpleHTTPServer (serves files in the current directory).
    # Test cases will request metadata and target files that have been
    # pre-generated in 'tuf/tests/repository_data', which will be served by the
    # SimpleHTTPServer launched here.  The test cases of this unit test assume 
    # the pre-generated metadata files have a specific structure, such
    # as a delegated role 'targets/role1', three target files, five key files,
    # etc.
    cls.SERVER_PORT = random.randint(30000, 45000)
    command = ['python', 'simple_server.py', str(cls.SERVER_PORT)]
    cls.server_process = subprocess.Popen(command, stderr=subprocess.PIPE)
    logger.info('Server process started.')
    logger.info('Server process id: '+str(cls.server_process.pid))
    logger.info('Serving on port: '+str(cls.SERVER_PORT))
    cls.url = 'http://localhost:'+str(cls.SERVER_PORT) + os.path.sep

    # NOTE: Following error is raised if a delay is not applied:
    # <urlopen error [Errno 111] Connection refused>
    time.sleep(.8)



  @classmethod 
  def tearDownClass(cls):
    # tearDownModule() is called after all the test cases have run.
    # http://docs.python.org/2/library/unittest.html#class-and-module-fixtures
   
    # Remove the temporary repository directory, which should contain all the
    # metadata, targets, and key files generated of all the test cases.
    shutil.rmtree(cls.temporary_directory)
    
    # Kill the SimpleHTTPServer process.
    if cls.server_process.returncode is None:
      logger.info('Server process '+str(cls.server_process.pid)+' terminated.')
      cls.server_process.kill()



  def setUp(self):
    # We are inheriting from custom class.
    unittest_toolbox.Modified_TestCase.setUp(self)
  
    # Copy the original repository files provided in the test folder so that
    # any modifications made to repository files are restricted to the copies.
    # The 'repository_data' directory is expected to exist in 'tuf/tests/'.
    original_repository_files = os.path.join(os.getcwd(), 'repository_data') 
    temporary_repository_root = \
      self.make_temp_directory(directory=self.temporary_directory)
  
    # The original repository, keystore, and client directories will be copied
    # for each test case. 
    original_repository = os.path.join(original_repository_files, 'repository')
    original_client = os.path.join(original_repository_files, 'client')
    original_keystore = os.path.join(original_repository_files, 'keystore')
    
    # Save references to the often-needed client repository directories.
    # Test cases need these references to access metadata and target files. 
    self.repository_directory = \
      os.path.join(temporary_repository_root, 'repository')
    self.client_directory = os.path.join(temporary_repository_root, 'client')
    self.keystore_directory = os.path.join(temporary_repository_root, 'keystore')
    
    # Copy the original 'repository', 'client', and 'keystore' directories
    # to the temporary repository the test cases can use.
    shutil.copytree(original_repository, self.repository_directory)
    shutil.copytree(original_client, self.client_directory)
    shutil.copytree(original_keystore, self.keystore_directory)
    
    # Set the url prefix required by the 'tuf/client/updater.py' updater.
    # 'path/to/tmp/repository' -> 'localhost:8001/tmp/repository'. 
    repository_basepath = self.repository_directory[len(os.getcwd()):]
    url_prefix = \
      'http://localhost:' + str(self.SERVER_PORT) + repository_basepath 
    
    # Setting 'tuf.conf.repository_directory' with the temporary client
    # directory copied from the original repository files.
    tuf.conf.repository_directory = self.client_directory 
    self.repository_mirrors = {'mirror1': {'url_prefix': url_prefix,
                                           'metadata_path': 'metadata',
                                           'targets_path': 'targets',
                                           'confined_target_dirs': ['']}}

    # Create the repository instance.  The test cases will use this client
    # updater to refresh metadata, fetch target files, etc.
    self.repository_updater = updater.Updater('test_repository',
                                              self.repository_mirrors)


  def tearDown(self):
    # Modified_TestCase.tearDown() automatically deletes temporary files and
    # directories that may have been created during each test case.
    unittest_toolbox.Modified_TestCase.tearDown(self)
    tuf.roledb.clear_roledb()


  def test_without_tuf(self):
    # Scenario:
    # 'timestamp.json' specifies the latest version of the repository files.
    # A client should only accept the same version number (specified in the
    # file) of the metadata, or greater.  A version number less than the one
    # currently trusted should be rejected.  A non-TUF client may use a
    # different mechanism for determining versions of metadata, but version
    # numbers in this integrations because that is what TUF uses.
    # 
    # Modify the repository's timestamp.json' so that a new version is generated
    # and accepted by the client, and backup the previous version.  The previous
    # is then returned the next time the client requests an update.  A non-TUF
    # client (without a way to detect older versions of metadata, and thus
    # updates) is expected to download older metadata and outdated files.
    # Verify that the older version of timestamp.json' is downloaded by the
    # non-TUF client.

    # Backup the current version of 'timestamp'.  It will be used as the
    # outdated version returned to the client.  The repository tool removes
    # obsolete metadadata, so do *not* save the backup version in the 
    # repository's metadata directory.
    timestamp_path = os.path.join(self.repository_directory, 'metadata',
                                  'timestamp.json')
    backup_timestamp = os.path.join(self.repository_directory,
                                    'timestamp.json.backup')
    shutil.copy(timestamp_path, backup_timestamp)
    
    # The fileinfo of the previous version is saved to verify that it is indeed
    # accepted by the non-TUF client.
    length, hashes = tuf.util.get_file_details(backup_timestamp)
    previous_fileinfo = tuf.formats.make_fileinfo(length, hashes)
    
    # Modify the timestamp file on the remote repository.
    repository = repo_tool.load_repository(self.repository_directory)
    key_file = os.path.join(self.keystore_directory, 'timestamp_key') 
    timestamp_private = repo_tool.import_ed25519_privatekey_from_file(key_file,
                                                                  'password')
    repository.timestamp.load_signing_key(timestamp_private)
    
    # Set an arbitrary expiration so that the repository tool generates a new
    # version.
    repository.timestamp.expiration = datetime.datetime(2030, 1, 1, 12, 12)
    repository.write()
    
    # Move the staged metadata to the "live" metadata.
    shutil.rmtree(os.path.join(self.repository_directory, 'metadata'))
    shutil.copytree(os.path.join(self.repository_directory, 'metadata.staged'),
                    os.path.join(self.repository_directory, 'metadata'))

    # Save the fileinfo of the new version generated to verify that it is
    # saved by the client. 
    length, hashes = tuf.util.get_file_details(timestamp_path)
    new_fileinfo = tuf.formats.make_fileinfo(length, hashes)

    url_prefix = self.repository_mirrors['mirror1']['url_prefix']
    url_file = os.path.join(url_prefix, 'metadata', 'timestamp.json')
    client_timestamp_path = os.path.join(self.client_directory, 'metadata',
                                         'current', 'timestamp.json')
   
    six.moves.urllib.request.urlretrieve(url_file, client_timestamp_path)
   
    length, hashes = tuf.util.get_file_details(client_timestamp_path)
    download_fileinfo = tuf.formats.make_fileinfo(length, hashes)
    
    # Verify 'download_fileinfo' is equal to the new version.
    self.assertEqual(download_fileinfo, new_fileinfo)

    # Restore the previous version of 'timestamp.json' on the remote repository
    # and verify that the non-TUF client downloads it (expected, but not ideal).
    shutil.move(backup_timestamp, timestamp_path)
    
    six.moves.urllib.request.urlretrieve(url_file, client_timestamp_path)
   
    length, hashes = tuf.util.get_file_details(client_timestamp_path)
    download_fileinfo = tuf.formats.make_fileinfo(length, hashes)
    
    # Verify 'download_fileinfo' is equal to the previous version.
    self.assertEqual(download_fileinfo, previous_fileinfo)
    self.assertNotEqual(download_fileinfo, new_fileinfo)



  def test_with_tuf(self):
    # The same scenario outlined in test_without_tuf() is followed here, except
    # with a TUF client (scenario description provided in the opening comment
    # block of that test case.) The TUF client performs a refresh of top-level
    # metadata, which also includes 'timestamp.json'.
    
    # Backup the current version of 'timestamp'.  It will be used as the
    # outdated version returned to the client.  The repository tool removes
    # obsolete metadadata, so do *not* save the backup version in the 
    # repository's metadata directory.
    timestamp_path = os.path.join(self.repository_directory, 'metadata',
                                  'timestamp.json')
    backup_timestamp = os.path.join(self.repository_directory,
                                    'timestamp.json.backup')
    shutil.copy(timestamp_path, backup_timestamp)
    
    # The fileinfo of the previous version is saved to verify that it is indeed
    # accepted by the non-TUF client.
    length, hashes = tuf.util.get_file_details(backup_timestamp)
    previous_fileinfo = tuf.formats.make_fileinfo(length, hashes)
    
    # Modify the timestamp file on the remote repository.
    repository = repo_tool.load_repository(self.repository_directory)
    key_file = os.path.join(self.keystore_directory, 'timestamp_key') 
    timestamp_private = repo_tool.import_ed25519_privatekey_from_file(key_file,
                                                                  'password')
    repository.timestamp.load_signing_key(timestamp_private)
    
    # Set an arbitrary expiration so that the repository tool generates a new
    # version.
    repository.timestamp.expiration = datetime.datetime(2030, 1, 1, 12, 12)
    repository.write()
   
    # Move the staged metadata to the "live" metadata.
    shutil.rmtree(os.path.join(self.repository_directory, 'metadata'))
    shutil.copytree(os.path.join(self.repository_directory, 'metadata.staged'),
                    os.path.join(self.repository_directory, 'metadata'))

    # Save the fileinfo of the new version generated to verify that it is
    # saved by the client. 
    length, hashes = tuf.util.get_file_details(timestamp_path)
    new_fileinfo = tuf.formats.make_fileinfo(length, hashes)

    # Refresh top-level metadata, including 'timestamp.json'.  Installation of
    # new version of 'timestamp.json' is expected.
    self.repository_updater.refresh()

    client_timestamp_path = os.path.join(self.client_directory, 'metadata',
                                         'current', 'timestamp.json')
    length, hashes = tuf.util.get_file_details(client_timestamp_path)
    download_fileinfo = tuf.formats.make_fileinfo(length, hashes)
    
    # Verify 'download_fileinfo' is equal to the new version.
    self.assertEqual(download_fileinfo, new_fileinfo)

    # Restore the previous version of 'timestamp.json' on the remote repository
    # and verify that the non-TUF client downloads it (expected, but not ideal).
    shutil.move(backup_timestamp, timestamp_path)
    logger.info('Moving the timestamp.json backup to the current version.')
    
    # Verify that the TUF client detects replayed metadata and refuses to
    # continue the update process.
    try:
      self.repository_updater.refresh()
   
    # Verify that the specific 'tuf.ReplayedMetadataError' is raised by each
    # mirror.
    except tuf.NoWorkingMirrorError as exception:
      for mirror_url, mirror_error in six.iteritems(exception.mirror_errors):
        url_prefix = self.repository_mirrors['mirror1']['url_prefix']
        url_file = os.path.join(url_prefix, 'metadata', 'timestamp.json')
       
        # Verify that 'timestamp.json' is the culprit.
        self.assertEqual(url_file, mirror_url)
        self.assertTrue(isinstance(mirror_error, tuf.ReplayedMetadataError))

    else:
      self.fail('TUF did not prevent a replay attack.')


if __name__ == '__main__':
  unittest.main()
