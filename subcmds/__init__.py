 1 #
 2 # Copyright (C) 2008 The Android Open Source Project
 3 #
 4 # Licensed under the Apache License, Version 2.0 (the "License");
 5 # you may not use this file except in compliance with the License.
 6 # You may obtain a copy of the License at
 7 #
 8 #      http://www.apache.org/licenses/LICENSE-2.0
 9 #
10 # Unless required by applicable law or agreed to in writing, software
11 # distributed under the License is distributed on an "AS IS" BASIS,
12 # WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
13 # See the License for the specific language governing permissions and
14 # limitations under the License.
15 
16 import os
17 
18 all_commands = {}
19 
20 my_dir = os.path.dirname(__file__)
21 for py in os.listdir(my_dir):
22   if py == '__init__.py':
23     continue
24 
25   if py.endswith('.py'):
26     name = py[:-3]
27 
28     clsn = name.capitalize()
29     while clsn.find('_') > 0:
30       h = clsn.index('_')
31       clsn = clsn[0:h] + clsn[h + 1:].capitalize()
32 
33     mod = __import__(__name__,
34                      globals(),
35                      locals(),
36                      ['%s' % name])
37     mod = getattr(mod, name)
38     try:
39       cmd = getattr(mod, clsn)()
40     except AttributeError:
41       raise SyntaxError('%s/%s does not define class %s' % (
42                          __name__, py, clsn))
43 
44     name = name.replace('_', '-')
45     cmd.NAME = name
46     all_commands[name] = cmd
47 
48 if 'help' in all_commands:
49   all_commands['help'].commands = all_commands
