# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function
from optparse import SUPPRESS_HELP
import sys

from command import Command, MirrorSafeCommand
from subcmds.sync import _PostRepoUpgrade
from subcmds.sync import _PostRepoFetch

"""
$ repo help selfupdate

Summary
-------
Update repo to the latest version

Usage: repo selfupdate

Options:
  -h, --help          show this help message and exit

  repo Version options:
    --no-repo-verify  do not verify repo source code

Description
-----------
The 'repo selfupdate' command upgrades repo to the latest version, if a
newer version is available.

Normally this is done automatically by 'repo sync' and does not need to
be performed by an end-user.
"""
class Selfupdate(Command, MirrorSafeCommand):
  common = False
  helpSummary = "Update repo to the latest version"
  helpUsage = """
%prog
"""
  helpDescription = """
The '%prog' command upgrades repo to the latest version, if a
newer version is available.

Normally this is done automatically by 'repo sync' and does not
need to be performed by an end-user.
"""

  """
  定义'repo selfupdate'命令的参数选项
  """
  def _Options(self, p):
    g = p.add_option_group('repo Version options')
    g.add_option('--no-repo-verify',
                 dest='no_repo_verify', action='store_true',
                 help='do not verify repo source code')
    g.add_option('--repo-upgraded',
                 dest='repo_upgraded', action='store_true',
                 help=SUPPRESS_HELP)

  """
  'repo selfupdate'命令中'selfupdate'操作的主函数。
  """
  def Execute(self, opt, args):
    rp = self.manifest.repoProject
    rp.PreSync()

    if opt.repo_upgraded:
      _PostRepoUpgrade(self.manifest)

    else:
      '''
      调用Sync_NetworkHalf，将远程数据抓fetch到本地库，但不影响工作目录和分支状态
      '''
      if not rp.Sync_NetworkHalf():
        print("error: can't update repo", file=sys.stderr)
        sys.exit(1)

      rp.bare_git.gc('--auto')
      '''
      使用前面Sync_NetworkHalf(fetch)拿到的数据更新repo自身库的工作目录
      '''
      _PostRepoFetch(rp,
                     no_repo_verify = opt.no_repo_verify,
                     verbose = True)
