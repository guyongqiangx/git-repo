#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2014 The Android Open Source Project
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
import imp
import os

"""
返回当前repo库下文件名为'repo'的脚本路径

如：'./repo/repo/repo'
"""
def WrapperPath():
  """
  获取与当前'wrapper.py'文件同一目录下的'repo'文件的路径，如:
  '.repo/repo/wrapper.py' --> ./repo/repo/repo'
  """
  return os.path.join(os.path.dirname(__file__), 'repo')

"""
加载repo库下的'./repo/repo/repo'文件作为Wrapper模块
"""
_wrapper_module = None
def Wrapper():
  global _wrapper_module
  if not _wrapper_module:
    """
    _wrapper_module由'./repo/repo/repo'文件通过'imp.load_source()'操作生成
    """
    _wrapper_module = imp.load_source('wrapper', WrapperPath())
  return _wrapper_module
