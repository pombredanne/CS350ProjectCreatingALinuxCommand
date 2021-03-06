#!/usr/bin/env python2
"""
arg_def.py -- Flag and arg defs for builtins.
"""
from __future__ import print_function

import sys

from _devbuild.gen.runtime_asdl import (
    cmd_value__Argv, flag_type, flag_type_t, value, value_t
)
from frontend import args
from mycpp import mylib

from typing import Union, List, Tuple, Dict, Any, Optional

# Similar to frontend/{option,builtin}_def.py
FLAG_SPEC = {}
FLAG_SPEC_AND_MORE = {}
OIL_SPEC ={}


def FlagSpec(builtin_name, typed=False):
  # type: (str, bool) -> _FlagSpec
  """
  """
  arg_spec = _FlagSpec(typed=typed)
  FLAG_SPEC[builtin_name] = arg_spec
  return arg_spec


def FlagSpecAndMore(name):
  # type: (str) -> _FlagSpecAndMore
  """
  For set, bin/oil.py ("main"), compgen -A, complete -A, etc.
  """
  arg_spec = _FlagSpecAndMore()
  FLAG_SPEC_AND_MORE[name] = arg_spec
  return arg_spec


def OilFlags(name):
  # type: (str) -> _OilFlags
  """
  For set, bin/oil.py ("main"), compgen -A, complete -A, etc.
  """
  arg_spec = _OilFlags()
  OIL_SPEC[name] = arg_spec
  return arg_spec


def Parse(spec_name, arg_r):
  # type: (str, args.Reader) -> args._Attributes
  """Parse argv using a given FlagSpec / FlagSpecAndMore."""
  spec = FLAG_SPEC[spec_name]
  return spec.Parse(arg_r)


def ParseCmdVal(spec_name, cmd_val):
  # type: (str, cmd_value__Argv) -> Tuple[args._Attributes, int]
  arg_r = args.Reader(cmd_val.argv, spids=cmd_val.arg_spids)
  arg_r.Next()  # move past the builtin name

  spec = FLAG_SPEC[spec_name]
  return spec.Parse(arg_r), arg_r.i


def All():
  # type: () -> Dict[str, Any]
  return FLAG_SPEC


def _FlagType(arg_type):
  # type: (Union[None, int, List[str]]) -> flag_type_t

  if arg_type is None:
    typ = flag_type.Bool()  # type: flag_type_t
  elif arg_type == args.Int:
    typ = flag_type.Int()
  elif arg_type == args.Float:
    typ = flag_type.Float()
  elif arg_type == args.String:
    typ = flag_type.Str()
  elif isinstance(arg_type, list):
    typ = flag_type.Enum(arg_type)
  else:
    raise AssertionError(arg_type)

  return typ


class _FlagSpec(object):
  """Parser for sh builtins, like 'read' or 'echo' (which has a special case).

  Usage:
    spec = args.FlagSpec()
    spec.ShortFlag('-a')
    opts, i = spec.Parse(argv)
  """
  def __init__(self, typed=False):
    # type: (bool) -> None

    # New style: eventually everything should be typed
    self.typed = typed

    self.arity0 = {}  # type: Dict[str, bool]  # {'r': _Action} for read -r
    self.arity1 = {}  # type: Dict[str, args.SetToArg]  # {'t': _Action} for read -t 1.0
    self.options = {}  # type: Dict[str, bool]  # e.g. for declare +r
    self.defaults = {}  # type: Dict[str, value_t]

    # For code generation.  Not used at runtime.
    self.fields = {}  # type: Dict[str, flag_type_t]  # for arg_types to use

  def PrintHelp(self, f):
    # type: (mylib.Writer) -> None
    if self.arity0:
      print('  arity 0:')
    for ch in self.arity0:
      print('    -%s' % ch)

    if self.arity1:
      print('  arity 1:')
    for ch in self.arity1:
      print('    -%s' % ch)

  def ShortFlag(self, short_name, arg_type=None, help=None):
    # type: (str, Optional[int], Optional[str]) -> None
    """
    This is very similar to ShortFlag for FlagSpecAndMore, except we have
    separate arity0 and arity1 dicts.
    """
    assert short_name.startswith('-'), short_name
    assert len(short_name) == 2, short_name

    char = short_name[1]
    if arg_type is None:
      self.arity0[char] = True
    else:
      self.arity1[char] = args.SetToArg(char, _FlagType(arg_type))

    # TODO: callers should pass flag_type
    if arg_type is None:
      typ = flag_type.Bool()  # type: flag_type_t
      default = value.Bool(False)  # type: value_t
    elif arg_type == args.Int:
      typ = flag_type.Int()
      default = value.Int(-1)
    elif arg_type == args.Float:
      typ = flag_type.Float()
      default = value.Float(0.0)
    elif arg_type == args.String:
      typ = flag_type.Str()
      default = value.Str('')
    elif isinstance(arg_type, list):
      typ = flag_type.Enum(arg_type)
      default = value.Str('')  # This isn't valid
    else:
      raise AssertionError(arg_type)

    if self.typed:
      self.defaults[char] = default
    else:
      # TODO: remove when all builtins converted
      self.defaults[char] = value.Undef()

    self.fields[char] = typ

  def ShortOption(self, char, help=None):
    # type: (str, Optional[str]) -> None
    """Define an option that can be turned off with + and on with -."""

    assert len(char) == 1  # 'r' for -r +r
    self.options[char] = True

    self.defaults[char] = value.Undef()
    # '+' or '-'.  TODO: Should we make it a bool?
    self.fields[char] = flag_type.Str()

  def ParseLikeEcho(self, argv):
    # type: (List[str]) -> Tuple[args._Attributes, int]
    """
    echo is a special case.  These work:
      echo -n
      echo -en
   
    - But don't respect --
    - doesn't fail when an invalid flag is passed
    """
    arg_r = args.Reader(argv)
    out = args._Attributes(self.defaults)

    while not arg_r.AtEnd():
      arg = arg_r.Peek()
      chars = arg[1:]
      if arg.startswith('-') and chars:
        # Check if it looks like -en or not.
        # NOTE: Changed to list comprehension to avoid
        # LOAD_CLOSURE/MAKE_CLOSURE.
        if not all([c in self.arity0 for c in arg[1:]]):
          break  # looks like args

        for ch in chars:
          assert ch in self.arity0
          out.SetTrue(ch)

      else:
        break  # Looks like an arg

      arg_r.Next()  # next arg

    return out, arg_r.i

  def Parse(self, arg_r):
    # type: (args.Reader) -> args._Attributes
    """For builtins to read args after we parse flags."""
    return args.Parse(self, arg_r)

  def ParseCmdVal(self, cmd_val):
    # type: (cmd_value__Argv) -> Tuple[args._Attributes, int]
    """Used by builtins that don't need arg_r.

    Note: It might be more flexible to return arg_r instead if i.  Then they
    can call arg_r.Rest(), etc.

    TODO: Remove in favor of ParseCmdVal above
    """
    arg_r = args.Reader(cmd_val.argv, spids=cmd_val.arg_spids)
    arg_r.Next()  # move past the builtin name
    return self.Parse(arg_r), arg_r.i

  def ParseArgv(self, argv):
    # type: (List[str]) -> Tuple[args._Attributes, int]
    """For tools/readlink.py -- no location info available."""
    arg_r = args.Reader(argv)
    return self.Parse(arg_r), arg_r.i


class _FlagSpecAndMore(object):
  """Parser for 'set' and 'sh', which both need to process shell options.

  Usage:
    spec = FlagSpecAndMore()
    spec.ShortFlag(...)
    spec.Option('u', 'nounset')
    spec.Parse(...)
  """

  def __init__(self):
    # type: () -> None
    self.actions_short = {}  # type: Dict[str, args._Action]  # {'-c': _Action}
    self.actions_long = {}  # type: Dict[str, args._Action]  # {'--rcfile': _Action}
    self.defaults = {}  # type: Dict[str, value_t]

    self.actions_short['o'] = args.SetNamedOption()  # -o and +o
    self.actions_short['O'] = args.SetNamedOption(shopt=True)  # -O and +O

  def InitActions(self):
    # type: () -> None
    self.actions_short['A'] = args.SetNamedAction()  # -A

  def ShortFlag(self, short_name, arg_type=None, default=None,
                quit_parsing_flags=False, help=None):
    # type: (str, int, Optional[Any], bool, Optional[str]) -> None
    """ -c """
    assert short_name.startswith('-'), short_name
    assert len(short_name) == 2, short_name

    char = short_name[1]
    if arg_type is None:
      assert quit_parsing_flags == False
      self.actions_short[char] = args.SetToTrue(char)
    else:
      self.actions_short[char] = args.SetToArg(
          char, _FlagType(arg_type), quit_parsing_flags=quit_parsing_flags)

    self.defaults[char] = args.PyToValue(default)

  def LongFlag(self,
               long_name,  # type: str
               arg_type=None,  # type: Union[List[str], None, int]
               default=None,  # type: Optional[Any]
               help=None,  # type: Optional[str]
               ):
    # type: (...) -> None
    """ --rcfile """
    assert long_name.startswith('--'), long_name

    name = long_name[2:]
    if arg_type is None:
      self.actions_long[long_name] = args.SetToTrue(name)
    else:
      self.actions_long[long_name] = args.SetToArg(name, _FlagType(arg_type))

    self.defaults[name] = args.PyToValue(default)

  def Option(self, short_flag, name, help=None):
    # type: (Optional[str], str, Optional[str]) -> None
    """Register an option that can be -e or -o errexit.

    Args:
      short_flag: 'e'
      name: errexit
    """
    attr_name = name
    if short_flag:
      assert not short_flag.startswith('-'), short_flag
      self.actions_short[short_flag] = args.SetOption(attr_name)

    self.actions_short['o'].Add(attr_name)  # type: ignore

  def ShoptOption(self, name, help=None):
    # type: (str, Optional[str]) -> None
    """Register an option like shopt -s nullglob

    Args:
      name: 'nullglob'
    """
    attr_name = name
    self.actions_short['O'].Add(attr_name)  # type: ignore

  def Action(self, short_flag, name):
    # type: (str, str) -> None
    """Register an action that can be -f or -A file.

    For the compgen builtin.

    Args:
      short_flag: 'f'
      name: 'file'
    """
    attr_name = name
    if short_flag:
      assert not short_flag.startswith('-'), short_flag
      self.actions_short[short_flag] = args.SetAction(attr_name)

    self.actions_short['A'].Add(attr_name)  # type: ignore

  def Parse(self, arg_r):
    # type: (args.Reader) -> args._Attributes
    return args.ParseMore(self, arg_r)


class _OilFlags(object):
  """Parser for oil command line tools and builtins.

  Tools:
    oshc ACTION [OPTION]... ARGS...
    oilc ACTION [OPTION]... ARG...
    opyc ACTION [OPTION]... ARG...

  Builtins:
    test -file /
    test -dir /
    Optionally accept test --file.

  Usage:
    spec = args.OilFlags()
    spec.Flag('-no-docstring')  # no short flag for simplicity?
    opts, i = spec.Parse(argv)

  Another idea:

    input = ArgInput(argv)
    action = input.ReadRequired(error='An action is required')

  The rest should be similar to Go flags.
  https://golang.org/pkg/flag/

  -flag
  -flag=x
  -flag x (non-boolean only)

  --flag
  --flag=x
  --flag x (non-boolean only)

  --flag=false  --flag=FALSE  --flag=0  --flag=f  --flag=F  --flag=False

  Disallow triple dashes though.

  FlagSet ?  That is just spec.

  spec.Arg('action') -- make it required!

  spec.Action()  # make it required, and set to 'action' or 'subaction'?

  if arg.action ==

    prefix= suffix= should be kwargs I think
    Or don't even share the actions?

  What about global options?  --verbose?

  You can just attach that to every spec, like DefineOshCommonOptions(spec).
  """
  def __init__(self):
    # type: () -> None
    self.arity1 = {}  # type: Dict[str, args._Action]
    self.defaults = {}  # type: Dict[str, value_t]  # attr name -> default value
    # (flag name, string) tuples, in order
    self.help_strings = []  # type: List[Tuple[str, str]]

  def Flag(self, name, arg_type, default=None, help=None):
    # type: (str, int, Optional[Any], Optional[str]) -> None
    """
    Args:
      name: e.g. '-no-docstring'
      arg_type: e.g. Str
    """
    assert name.startswith('-') and not name.startswith('--'), name

    attr_name = name[1:].replace('-', '_')
    if arg_type == args.Bool:
      self.arity1[attr_name] = args.SetBoolToArg(attr_name)
    else:
      self.arity1[attr_name] = args.SetToArg(attr_name, _FlagType(arg_type))

    self.defaults[attr_name] = args.PyToValue(default)

  def Parse(self, arg_r):
    # type: (args.Reader) -> Tuple[args._Attributes, int]
    return args.ParseOil(self, arg_r)

  def ParseArgv(self, argv):
    # type: (List[str]) -> Tuple[args._Attributes, int]
    """For tools/readlink.py -- no location info available."""
    arg_r = args.Reader(argv)
    return self.Parse(arg_r)


# TODO:
# spec = FlagSpec(builtin_i.export)
#
# And then later:
# 
# attrs = args.Parse(builtin_i.export, arg_r)
# arg = arg_types.export(attrs)
#
# Does this mean that builtins should take:
# Run(attrs, arg_r) ?
# I think that makes more sense...
# Yeah _Builtin should define Run(cmd_val) to parse it and call
# _Run(attrs, arg_reader)
#   

# It will look up the spec dynamically and then call Parse() on it I think.

EXPORT_SPEC = FlagSpec('export_', typed=True)
EXPORT_SPEC.ShortFlag('-n')
EXPORT_SPEC.ShortFlag('-f')  # stubbed
EXPORT_SPEC.ShortFlag('-p')


READONLY_SPEC = FlagSpec('readonly', typed=True)

# TODO: Check the consistency of -a and -A against values, here and below.
READONLY_SPEC.ShortFlag('-a')
READONLY_SPEC.ShortFlag('-A')
READONLY_SPEC.ShortFlag('-p')


NEW_VAR_SPEC = FlagSpec('new_var', typed=True)

# print stuff
NEW_VAR_SPEC.ShortFlag('-f')
NEW_VAR_SPEC.ShortFlag('-F')
NEW_VAR_SPEC.ShortFlag('-p')

NEW_VAR_SPEC.ShortFlag('-g')  # Look up in global scope

# Options +r +x +n
NEW_VAR_SPEC.ShortOption('x')  # export
NEW_VAR_SPEC.ShortOption('r')  # readonly
NEW_VAR_SPEC.ShortOption('n')  # named ref

# Common between readonly/declare
NEW_VAR_SPEC.ShortFlag('-a')
NEW_VAR_SPEC.ShortFlag('-A')


UNSET_SPEC = FlagSpec('unset', typed=True)
UNSET_SPEC.ShortFlag('-v')
UNSET_SPEC.ShortFlag('-f')
