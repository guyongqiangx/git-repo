# -*- coding: utf-8 -*-
#
# Copyright (C) 2008 The Android Open Source Project
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
import os
REPO_TRACE = 'REPO_TRACE'

"""
有两种办法打开Trace消息功能：
1. 设置环境变量'REPO_TRACE'为1；
2. 调用SetTrace()函数；
"""

"""
检查环境变量'REPO_TRACE'是否为1来设置'_TRACE'

所以，如果想跟踪某个命令的执行但是又不想设置环境变量，可以运行命令时临时指定，
如：'REPO_TRACE=1 repo manifest -r'
"""
try:
  _TRACE = os.environ[REPO_TRACE] == '1'
except KeyError:
  _TRACE = False

"""
返回Trace状态
"""
def IsTrace():
  return _TRACE

"""
打开Trace功能

在main.py的_Run(argv)函数中，如果设置了'--trace'选项，则会调用SetTrace()打开Trace消息功能
"""
def SetTrace():
  global _TRACE
  _TRACE = True

"""
打印Trace消息

只有当Trace功能打开时，才会调用print()函数显示Trace消息，否则什么都不做。
"""
def Trace(fmt, *args):
  if IsTrace():
    print(fmt % args, file=sys.stderr)
