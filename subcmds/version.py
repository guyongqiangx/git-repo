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
import sys
from command import Command, MirrorSafeCommand
from git_command import git
from git_refs import HEAD

"""
$ repo help version

Summary
-------
Display the version of repo

Usage: repo version

Options:
  -h, --help  show this help message and exit
$
$ repo version
repo version v1.12.37
       (from https://gerrit.googlesource.com/git-repo)
repo launcher version 1.23
       (from /home/ygu/bin/repo)
git version 1.9.1
Python 2.7.6 (default, Nov 13 2018, 12:45:42)
[GCC 4.8.4]
"""
class Version(Command, MirrorSafeCommand):
  wrapper_version = None
  wrapper_path = None

  common = False
  helpSummary = "Display the version of repo"
  helpUsage = """
%prog
"""

  """
  'repo version'命令中'version'操作的主函数。
  """
  def Execute(self, opt, args):
    rp = self.manifest.repoProject
    rem = rp.GetRemote(rp.remote.name)

    """
    取得repo库HEAD引用指向的版本以及remote源的地址
    """
    print('repo version %s' % rp.work_git.describe(HEAD))
    print('       (from %s)' % rem.url)

    """
    这访问main.py中设置的的全局变量Version, 而不是这里的Version类自身, 因为对于后者的访问应该是self

    wrapper_path和wrapper_version在main.py中已经更新好了。
    """
    if Version.wrapper_path is not None:
      print('repo launcher version %s' % Version.wrapper_version)
      print('       (from %s)' % Version.wrapper_path)

    """
    获取git工具的版本
    """
    print(git.version().strip())
    print('Python %s' % sys.version)
