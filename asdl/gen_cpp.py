"""
gen_cpp.py - Generate C++ classes from an ASDL schema.

TODO:

- Integrate some of the lessons here:  
  - https://github.com/oilshell/blog-code/tree/master/asdl
  - And maybe mycpp/target_lang.cc

- pretty printing methods
  - so asdl/format.py get translated?

- NoOp needs to be instantiated without args?
- dict becomes Dict[str, str] ?
- how to handle UserType(id) ?

- How do optional ASDL values like int? work?  Use C++ default values?
  - This means that all the optionals have to be on the end.  That seems OK.
  - I guess that's how Python does it.
"""
from __future__ import print_function

import sys

from collections import defaultdict

from asdl import asdl_
from asdl import visitor
from core.util import log

_ = log


# Used by core/asdl_gen.py to generate _devbuild/gen/osh-types.h, with
# lex_mode__*
class CEnumVisitor(visitor.AsdlVisitor):

  def VisitSimpleSum(self, sum, name, depth):
    # Just use #define, since enums aren't namespaced.
    for i, variant in enumerate(sum.types):
      self.Emit('#define %s__%s %d' % (name, variant.name, i + 1), depth)
    self.Emit("", depth)


_PRIMITIVES = {
    'string': 'Str*',  # declared in mylib.h
    'int': 'int',
    'float': 'double',
    'bool': 'bool',
    'any': 'void*',
    # TODO: frontend/syntax.asdl should properly import id enum instead of
    # hard-coding it here.
    'id': 'Id_t',
}


class ForwardDeclareVisitor(visitor.AsdlVisitor):
  """Print forward declarations.

  ASDL allows forward references of types, but C++ doesn't.
  """
  def VisitCompoundSum(self, sum, name, depth):
    self.Emit("class %(name)s_t;" % locals(), depth)

  def VisitProduct(self, product, name, depth):
    self.Emit("class %(name)s;" % locals(), depth)

  def EmitFooter(self):
    self.Emit("", 0)  # blank line


def _GetMapType(type_expr):
  """
  TODO: Make this recursive.  Should be TypeExprToCpp

  And consolidate with _GetInnerCppType.  That is used for Array, and this for
  Map.
  """
  assert len(type_expr.children) == 0, type_expr

  # This includes asdl_.SimpleSum
  if type_expr.resolved:
    if isinstance(type_expr.resolved, asdl_.Sum):
      return '%s_t' % type_expr.name
    if isinstance(type_expr.resolved, asdl_.Product):
      return type_expr.name

  # TODO: Need to use field.resolved_type
  return _PRIMITIVES[type_expr.name]


def _GetInnerCppType(field):
  type_name = field.TypeName()

  if type_name == 'map':
    k_type = _GetMapType(field.typ.children[0])
    v_type = _GetMapType(field.typ.children[1])
    return 'Dict<%s, %s>*' % (k_type, v_type)

  cpp_type = _PRIMITIVES.get(type_name)
  if cpp_type is not None:
    # Annotations like int? or Id?  are NO-OPS now.
    # The user should reserve a Id.Unknown_Tok or -1.
    if field.IsMaybe() and not cpp_type.endswith('*'):
      # e.g. Id_t*
      # TODO: Use Id.Unknown_Tok for id?
      # - use runtime.NO_STEP for int? step.  That could be ZERO?
      # - although 5..6..0 is allowed.  Probably -MAXINT
      # Need to make sure int parsing doesn't overflow.
      #raise AssertionError(field)
      print('field %s' % field, file=sys.stderr)
    return cpp_type

  if field.resolved_type:
    if isinstance(field.resolved_type, asdl_.SimpleSum):
      # Use the enum instead of the class.
      return "%s_e" % type_name
    if isinstance(field.resolved_type, asdl_.Sum):
      # Everything is a pointer for now.  No references.
      return "%s_t*" % type_name

  return '%s*' % type_name


class ClassDefVisitor(visitor.AsdlVisitor):
  """Generate C++ declarations and type-safe enums."""

  def __init__(self, f, e_suffix=True,
               pretty_print_methods=True, simple_int_sums=None,
               debug_info=None):
    """
    Args:
      f: file to write to
      debug_info: dictionary fill in with info for GDB
    """
    visitor.AsdlVisitor.__init__(self, f)
    self.e_suffix = e_suffix
    self.pretty_print_methods = pretty_print_methods
    self.simple_int_sums = simple_int_sums or []
    self.debug_info = debug_info if debug_info is not None else {}

    self._shared_type_tags = {}
    self._product_counter = 1000  # start it high

    self._products = []
    self._product_bases = defaultdict(list)

  def _GetCppType(self, field):
    """Return a string for the C++ name of the type."""

    # TODO: The once instance of 'dict' needs an overhaul!
    # Right now it's untyped in ASDL.
    # I guess is should be Dict[str, str] for the associative array contents?
    c_type = _GetInnerCppType(field)
    if field.IsArray():
      return 'List<%s>*' % c_type
    else:
      return c_type

  def _EmitEnum(self, sum, sum_name, depth, strong=False, is_simple=False):
    enum = []
    int_to_type = {}
    for i, variant in enumerate(sum.types):
      if variant.shared_type:  # Copied from gen_python.py
        tag_num = self._shared_type_tags[variant.shared_type]
        # e.g. double_quoted may have base types expr_t, word_part_t
        base_class = sum_name + '_t'
        bases = self._product_bases[variant.shared_type]
        if base_class in bases:
          raise RuntimeError(
              "Two tags in sum %r refer to product type %r" %
              (sum_name, variant.shared_type))
        else:
          bases.append(base_class)
        type_str = variant.shared_type
      else:
        tag_num = i + 1
        type_str = '%s__%s' % (sum_name, variant.name)
      int_to_type[tag_num] = type_str
      enum.append((variant.name, tag_num))  # zero is reserved

    if strong:
      enum_name = '%s_e' % sum_name if self.e_suffix else sum_name

      # Simple sum types can be STRONG since there's no possibility of multiple
      # inheritance!

      self.Emit('enum class %s {' % enum_name, depth)
      for name, tag_num in enum:
        self.Emit('%s = %d,' % (name, tag_num), depth + 1)
      self.Emit('};', depth)

      # type alias to match Python code
      self.Emit('typedef %s %s_t;' % (enum_name, sum_name), depth)
      self.Emit('', depth)

      self.Emit('const char* %s_str(%s tag);' % (sum_name, enum_name), depth)
      self.Emit('', depth)

    else:
      if is_simple:
        enum_name = '%s_i' % sum_name if self.e_suffix else sum_name
      else:
        enum_name = '%s_e' % sum_name if self.e_suffix else sum_name

      self.Emit('namespace %s {' % enum_name, depth)
      for name, tag_num in enum:
        self.Emit('const int %s = %d;' % (name, tag_num), depth + 1)

      if is_simple:
        # Help in sizing array.  Note that we're 1-based.
        self.Emit('const int %s = %d;' % ('ARRAY_SIZE', len(enum) + 1),
                  depth + 1)
      self.Emit('};', depth)

      self.Emit('', depth)

      self.Emit('const char* %s_str(int tag);' % sum_name, depth)
      self.Emit('', depth)

    return int_to_type

  def VisitSimpleSum(self, sum, name, depth):
    if name in self.simple_int_sums:
      self._EmitEnum(sum, name, depth, strong=False, is_simple=True)
      self.Emit('typedef int %s_t;' % name)
      self.Emit('')
    else:
      self._EmitEnum(sum, name, depth, strong=True)

  def VisitCompoundSum(self, sum, sum_name, depth):
    # This is a sign that Python needs string interpolation!!!
    def Emit(s, depth=depth):
      self.Emit(s % sys._getframe(1).f_locals, depth)

    int_to_type = self._EmitEnum(sum, sum_name, depth)

    # Only add debug info for compound sums.
    self.debug_info['%s_t' % sum_name] = int_to_type

    # TODO: DISALLOW_COPY_AND_ASSIGN on this class and others?

    # This is the base class.
    Emit('class %(sum_name)s_t {')
    # Can't be constructed directly.  Note: this shows up in uftrace in debug
    # mode, e.g. when we intantiate Token.  Do we need it?
    Emit(' protected:')
    Emit('  %s_t() {}' % sum_name)
    Emit(' public:')
    Emit('  int tag_() {')
    # There's no inheritance relationship, so we have to reinterpret_cast.
    Emit('    return reinterpret_cast<Obj*>(this)->tag;')
    Emit('  }')

    if self.pretty_print_methods:
      for abbrev in 'PrettyTree', '_AbbreviatedTree', 'AbbreviatedTree':
        self.Emit('  hnode_t* %s();' % abbrev)

    Emit('  DISALLOW_COPY_AND_ASSIGN(%(sum_name)s_t)')
    Emit('};')
    Emit('')

    for variant in sum.types:
      if variant.shared_type:
        # Don't generate a class.
        pass
      else:
        super_name = '%s_t' % sum_name
        tag = 'static_cast<uint16_t>(%s_e::%s)' % (sum_name, variant.name)
        class_name = '%s__%s' % (sum_name, variant.name)
        self._GenClass(variant, sum.attributes, class_name, [super_name],
                       depth, tag)

    # Allow expr::Const in addition to expr__Const.
    Emit('namespace %(sum_name)s {')
    for variant in sum.types:
      if not variant.shared_type:
        variant_name = variant.name
        Emit('  typedef %(sum_name)s__%(variant_name)s %(variant_name)s;')
    Emit('}')
    Emit('')

  def _DefaultValue(self, field):
    type_name = field.TypeName()
    if field.IsArray():
      default = 'new List<%s>()' % _GetInnerCppType(field)
    else:
      if type_name == 'int':
        default = '-1'
      elif type_name == 'id':  # hard-coded HACK
        default = '-1'
      elif type_name == 'bool':
        default = 'false'
      elif type_name == 'float':
        default = '0.0'  # or should it be NaN?
      elif type_name == 'string':
        default = 'new Str("")'
      elif type_name == 'map':
        default = 'nullptr'
      else:
        if field.resolved_type and isinstance(field.resolved_type, asdl_.SimpleSum):
          sum_type = field.resolved_type
          # Just make it the first variant.  We could define "Undef" for
          # each enum, but it doesn't seem worth it.
          default = '%s_e::%s' % (type_name, sum_type.types[0].name)
        else:
          default = 'nullptr'
    return default

  def _GenClass(self, ast_node, attributes, class_name, base_classes, depth, tag):
    """For Product and Constructor."""
    if base_classes:
      bases = ', '.join('public %s' % b for b in base_classes)
      self.Emit("class %s : %s {" % (class_name, bases), depth)
    else:
      self.Emit("class %s {" % class_name, depth)
    self.Emit(" public:", depth)

    tag_init = 'tag(%s)' % tag
    all_fields = ast_node.fields + attributes

    if ast_node.fields:  # Don't emit for constructors with no fields
      default_inits = [tag_init]
      for field in all_fields:
        default = self._DefaultValue(field)
        default_inits.append('%s(%s)' % (field.name, default))

      # Constructor with ZERO args
      self.Emit("  %s() : %s {" %
          (class_name, ', '.join(default_inits)), depth)
      self.Emit("  }")

    params = []
    # All product types and variants have a tag
    inits = [tag_init]

    for f in ast_node.fields:
      params.append('%s %s' % (self._GetCppType(f), f.name))
      inits.append('%s(%s)' % (f.name, f.name))
    for f in attributes:  # spids are initialized separately
      inits.append('%s(%s)' % (f.name, self._DefaultValue(f)))

    # Constructor with N args
    self.Emit("  %s(%s) : %s {" %
        (class_name, ', '.join(params), ', '.join(inits)), depth)
    self.Emit("  }")

    #
    # Members
    #
    self.Emit('  uint16_t tag;')
    for field in all_fields:
      self.Emit("  %s %s;" % (self._GetCppType(field), field.name))

    if self.pretty_print_methods:
      for abbrev in 'PrettyTree', '_AbbreviatedTree', 'AbbreviatedTree':
        self.Emit('  hnode_t* %s();' % abbrev, depth)

    self.Emit('')
    self.Emit('  DISALLOW_COPY_AND_ASSIGN(%s)' % class_name)
    self.Emit('};', depth)
    self.Emit('', depth)

  def VisitProduct(self, product, name, depth):
    self._shared_type_tags[name] = self._product_counter
    # Create a tuple of _GenClass args to create LAST.  They may inherit from
    # sum types that have yet to be defined.
    self._products.append(
        (product, product.attributes, name, depth, self._product_counter)
    )
    self._product_counter += 1

  def EmitFooter(self):
    # Now generate all the product types we deferred.
    for args in self._products:
      ast_node, attributes, name, depth, tag_num = args
      # Figure out base classes AFTERWARD.
      bases = self._product_bases[name]
      if not bases:
        bases = ['Obj']
      self._GenClass(ast_node, attributes, name, bases, depth, tag_num)


class MethodDefVisitor(visitor.AsdlVisitor):
  """Generate the body of pretty printing methods.

  We have to do this in another pass because types and schemas have circular
  dependencies.
  """
  def __init__(self, f, e_suffix=True, pretty_print_methods=True,
               simple_int_sums=None):
    visitor.AsdlVisitor.__init__(self, f)
    self.e_suffix = e_suffix
    self.pretty_print_methods = pretty_print_methods
    self.simple_int_sums = simple_int_sums or []

  def _CodeSnippet(self, abbrev, field, var_name):
    none_guard = False
    type_name = field.TypeName()

    if type_name == 'bool':
      code_str = "new hnode__Leaf(%s ? runtime::TRUE_STR : runtime::FALSE_STR, color_e::OtherConst)" % var_name

    elif type_name == 'int':
      code_str = 'new hnode__Leaf(str(%s), color_e::OtherConst)' % var_name

    elif type_name == 'float':
      code_str = 'new hnode__Leaf(str(%s), color_e::OtherConst)' % var_name

    elif type_name == 'string':
      code_str = 'runtime::NewLeaf(%s, color_e::StringConst)' % var_name

    elif type_name == 'any':
      # This is used for value.Obj().
      code_str = 'new hnode__External(%s)' % var_name

    elif type_name == 'map':
      # TODO: Is this valid?
      code_str = 'new hnode__External(%s)' % var_name

    elif type_name == 'id':
      # TODO: Remove hard-coded Id?
      code_str = 'new hnode__Leaf(new Str(Id_str(%s)), color_e::UserType)' % var_name
      none_guard = True  # otherwise MyPy complains about foo.name

    elif field.resolved_type:
      if isinstance(field.resolved_type, asdl_.SimpleSum):
        code_str = 'new hnode__Leaf(new Str(%s_str(%s)), color_e::TypeName)' % (
            field.TypeName(), var_name)
        none_guard = True  # otherwise MyPy complains about foo.name
      else:
        code_str = '%s->%s()' % (var_name, abbrev)
        none_guard = True

    else:
      raise AssertionError(field)

    return code_str, none_guard

  def _EmitCodeForField(self, abbrev, field, counter):
    """Generate code that returns an hnode for a field."""
    out_val_name = 'x%d' % counter

    if field.IsArray():
      iter_name = 'i%d' % counter

      self.Emit('  if (this->%s && len(this->%s)) {  // ArrayType' % (field.name, field.name))
      self.Emit('    hnode__Array* %s = new hnode__Array(new List<hnode_t*>());' % out_val_name)
      item_type = _GetInnerCppType(field)
      self.Emit('    for (ListIter<%s>it(this->%s); !it.Done(); it.Next()) {'
                % (item_type, field.name))
      self.Emit('      %s %s = it.Value();' % (item_type, iter_name))
      child_code_str, _ = self._CodeSnippet(abbrev, field, iter_name)
      self.Emit('      %s->children->append(%s);' % (out_val_name, child_code_str))
      self.Emit('    }')
      self.Emit('    L->append(new field(new Str("%s"), %s));' % (field.name, out_val_name))
      self.Emit('  }')

    elif field.IsMaybe():
      self.Emit('  if (this->%s) {  // MaybeType' % field.name)
      child_code_str, _ = self._CodeSnippet(abbrev, field, 'this->%s' % field.name)
      self.Emit('    hnode_t* %s = %s;' % (out_val_name, child_code_str))
      self.Emit('    L->append(new field(new Str("%s"), %s));' % (field.name, out_val_name))
      self.Emit('  }')

    else:
      var_name = 'this->%s' % field.name
      code_str, obj_none_guard = self._CodeSnippet(abbrev, field, var_name)

      depth = self.current_depth
      if obj_none_guard:  # to satisfy MyPy type system
        pass
      self.Emit('  hnode_t* %s = %s;' % (out_val_name, code_str), depth)

      self.Emit('  L->append(new field(new Str("%s"), %s));' % (field.name, out_val_name), depth)

  def _EmitPrettyPrintMethods(self, class_name, all_fields, ast_node):
    if not self.pretty_print_methods:
      return

    pretty_cls_name = class_name.replace('__', '.')  # used below

    #
    # PrettyTree
    #

    # TODO: Create shared constants for the sum/variant names.  Both const
    # char* and Str*.

    self.Emit('')
    self.Emit('hnode_t* %s::PrettyTree() {' % class_name)
    self.Emit('  hnode__Record* out_node = runtime::NewRecord(new Str("%s"));' % pretty_cls_name)
    if all_fields:
      self.Emit('  List<field*>* L = out_node->fields;')

    # Use the runtime type to be more like asdl/format.py
    for local_id, field in enumerate(all_fields):
      #log('%s :: %s', field_name, field_desc)
      self.Indent()
      self._EmitCodeForField('PrettyTree', field, local_id)
      self.Dedent()
      self.Emit('')
    self.Emit('  return out_node;')
    self.Emit('}')

    #
    # _AbbreviatedTree
    #

    self.Emit('')
    self.Emit('hnode_t* %s::_AbbreviatedTree() {' % class_name)
    self.Emit('  hnode__Record* out_node = runtime::NewRecord(new Str("%s"));' % pretty_cls_name)
    if ast_node.fields:
      self.Emit('  List<field*>* L = out_node->fields;')

    # NO attributes in abbreviated version
    for local_id, field in enumerate(ast_node.fields):
      self.Indent()
      self._EmitCodeForField('AbbreviatedTree', field, local_id)
      self.Dedent()
      self.Emit('')
    self.Emit('  return out_node;')
    self.Emit('}')
    self.Emit('')

    self.Emit('hnode_t* %s::AbbreviatedTree() {' % class_name)
    abbrev_name = '_%s' % class_name

    # STUB
    self.abbrev_mod_entries = []

    if abbrev_name in self.abbrev_mod_entries:
      self.Emit('  hnode_t* p = %s();' % abbrev_name)
      # If the user function didn't return anything, fall back.
      self.Emit('  return p ? p : _AbbreviatedTree();')
    else:
      self.Emit('  return _AbbreviatedTree();')
    self.Emit('}')

  def _EmitStrFunction(self, sum, sum_name, depth, strong=False, simple=False):
    if self.e_suffix:  # note: can be i_suffix too
      if simple:
        enum_name = '%s_i' % sum_name
      else:
        enum_name = '%s_e' % sum_name
    else:
      enum_name = sum_name

    if strong:
      self.Emit('const char* %s_str(%s tag) {' % (sum_name, enum_name), depth)
    else:
      self.Emit('const char* %s_str(int tag) {' % sum_name, depth)

    self.Emit('  switch (tag) {', depth)
    for variant in sum.types:
      self.Emit('case %s::%s:' % (enum_name, variant.name), depth + 1)
      self.Emit('  return "%s.%s";' % (sum_name, variant.name), depth + 1)

    # NOTE: This happened in real life, maybe due to casting.  TODO: assert(0)
    # instead?

    self.Emit('default:', depth + 1)
    self.Emit('  assert(0);', depth + 1)
    
    self.Emit('  }', depth)
    self.Emit('}', depth)

  def VisitSimpleSum(self, sum, name, depth):
    if name in self.simple_int_sums:
      self._EmitStrFunction(sum, name, depth, strong=False, simple=True)
    else:
      self._EmitStrFunction(sum, name, depth, strong=True)

  def VisitCompoundSum(self, sum, sum_name, depth):
    self._EmitStrFunction(sum, sum_name, depth)

    if not self.pretty_print_methods:
      return

    for variant in sum.types:
      if variant.shared_type:
        pass
      else:
        super_name = '%s_t' % sum_name
        all_fields = variant.fields + sum.attributes
        tag = '%s_e::%s' % (sum_name, variant.name)
        class_name = '%s__%s' % (sum_name, variant.name)
        self._EmitPrettyPrintMethods(class_name, all_fields, variant)

    # Emit dispatch WITHOUT using 'virtual'
    for abbrev in 'PrettyTree', '_AbbreviatedTree', 'AbbreviatedTree':
      self.Emit('')
      self.Emit('hnode_t* %s_t::%s() {' % (sum_name, abbrev))
      self.Emit('  switch (this->tag_()) {', depth)

      for variant in sum.types:
        if variant.shared_type:
          subtype_name = variant.shared_type
        else:
          subtype_name = '%s__%s' % (sum_name, variant.name)

        self.Emit('  case %s_e::%s: {' % (sum_name, variant.name), depth)
        self.Emit('    %s* obj = static_cast<%s*>(this);' %
                  (subtype_name, subtype_name), depth)
        self.Emit('    return obj->%s();' % abbrev, depth)
        self.Emit('  }', depth)

      self.Emit('  default:', depth)
      self.Emit('    assert(0);', depth)

      self.Emit('  }')
      self.Emit('}')

  def VisitProduct(self, product, name, depth):
    #self._GenClass(product, product.attributes, name, None, depth)
    all_fields = product.fields + product.attributes
    self._EmitPrettyPrintMethods(name, all_fields, product)
