#!/usr/bin/env python
"""Message registry for apitools."""

import collections
import contextlib
import json

from protorpc import descriptor
from protorpc import messages

from apitools.gen import extended_descriptor

TypeInfo = collections.namedtuple('TypeInfo', ('type_name', 'variant'))


class MessageRegistry(object):
  """Registry for message types.

  This closely mirrors a messages.FileDescriptor, but adds additional
  attributes (such as message and field descriptions) and some extra
  code for validation and cycle detection.
  """

  # Type information from these two maps comes from here:
  #  https://developers.google.com/discovery/v1/type-format
  PRIMITIVE_TYPE_INFO_MAP = {
      'string': TypeInfo(type_name='string',
                         variant=messages.StringField.DEFAULT_VARIANT),
      'integer': TypeInfo(type_name='integer',
                          variant=messages.IntegerField.DEFAULT_VARIANT),
      'boolean': TypeInfo(type_name='boolean',
                          variant=messages.BooleanField.DEFAULT_VARIANT),
      'number': TypeInfo(type_name='number',
                         variant=messages.FloatField.DEFAULT_VARIANT),
      }

  PRIMITIVE_FORMAT_MAP = {
      'int32': TypeInfo(type_name='integer',
                        variant=messages.Variant.INT32),
      'uint32': TypeInfo(type_name='integer',
                         variant=messages.Variant.UINT32),
      'int64': TypeInfo(type_name='string',
                        variant=messages.Variant.INT64),
      'uint64': TypeInfo(type_name='string',
                         variant=messages.Variant.UINT64),
      'double': TypeInfo(type_name='number',
                         variant=messages.Variant.DOUBLE),
      'float': TypeInfo(type_name='number',
                        variant=messages.Variant.FLOAT),
      'byte': TypeInfo(type_name='byte',
                       variant=messages.BytesField.DEFAULT_VARIANT),
      'date': TypeInfo(type_name='protorpc.message_types.DateTimeMessage',
                       variant=messages.Variant.MESSAGE),
      'date-time': TypeInfo(type_name='protorpc.message_types.DateTimeMessage',
                            variant=messages.Variant.MESSAGE),
      }

  def __init__(self, client_info, names, description,
               root_package_dir, base_files_package):
    self.__names = names
    self.__client_info = client_info
    self.__package = client_info.package
    self.__description = description
    self.__root_package_dir = root_package_dir
    self.__base_files_package = base_files_package
    self.__file_descriptor = extended_descriptor.ExtendedFileDescriptor(
        package=self.__package, description=self.__description)
    # Add required imports
    self.__file_descriptor.additional_imports = [
        'from protorpc import messages',
        ]
    # Map from scoped names (i.e. Foo.Bar) to MessageDescriptors.
    self.__message_registry = collections.OrderedDict()
    # A set of types that we're currently adding (for cycle detection).
    self.__nascent_types = set()
    # A set of types for which we've seen a reference but no
    # definition; if this set is nonempty, validation fails.
    self.__unknown_types = set()
    # Used for tracking paths during message creation
    self.__current_path = []
    # Where to register created messages
    self.__current_env = self.__file_descriptor
    # TODO(craigcitro): Add a `Finalize` method.

  @property
  def file_descriptor(self):
    self.Validate()
    return self.__file_descriptor

  def WriteProtoFile(self, out):
    """Write the messages file to out as proto."""
    self.Validate()
    extended_descriptor.WriteMessagesFile(
        self.__file_descriptor, self.__package, self.__client_info.version, out)

  def WriteFile(self, out):
    """Write the messages file to out."""
    self.Validate()
    extended_descriptor.WritePythonFile(
        self.__file_descriptor, self.__package, self.__client_info.version, out)

  def Validate(self):
    mysteries = self.__nascent_types or self.__unknown_types
    if mysteries:
      raise ValueError('Malformed MessageRegistry: %s' % mysteries)

  def __ComputeFullName(self, name):
    return '.'.join(map(unicode, self.__current_path[:] + [name]))

  def __AddImport(self, new_import):
    if new_import not in self.__file_descriptor.additional_imports:
      self.__file_descriptor.additional_imports.append(new_import)

  def __DeclareDescriptor(self, name):
    self.__nascent_types.add(self.__ComputeFullName(name))

  def __RegisterDescriptor(self, new_descriptor):
    if not isinstance(new_descriptor, (
        extended_descriptor.ExtendedMessageDescriptor,
        extended_descriptor.ExtendedEnumDescriptor)):
      raise ValueError('Cannot add descriptor of type %s' % (
          type(new_descriptor),))
    full_name = self.__ComputeFullName(new_descriptor.name)
    if full_name in self.__message_registry:
      raise ValueError('Attempt to re-register descriptor %s' % full_name)
    if full_name not in self.__nascent_types:
      raise ValueError('Directly adding types is not supported')
    new_descriptor.full_name = full_name
    self.__message_registry[full_name] = new_descriptor
    if isinstance(new_descriptor,
                  extended_descriptor.ExtendedMessageDescriptor):
      self.__current_env.message_types.append(new_descriptor)
    elif isinstance(new_descriptor, extended_descriptor.ExtendedEnumDescriptor):
      self.__current_env.enum_types.append(new_descriptor)
    self.__unknown_types.discard(full_name)
    self.__nascent_types.remove(full_name)

  def LookupDescriptor(self, name):
    return self.__GetDescriptorByName(name)

  def LookupDescriptorOrDie(self, name):
    message_descriptor = self.LookupDescriptor(name)
    if message_descriptor is None:
      raise ValueError('No message descriptor named "%s"', name)
    return message_descriptor

  def __GetDescriptor(self, name):
    return self.__GetDescriptorByName(self.__ComputeFullName(name))

  def __GetDescriptorByName(self, name):
    if name in self.__message_registry:
      return self.__message_registry[name]
    if name in self.__nascent_types:
      raise ValueError(
          'Cannot retrieve type currently being created: %s' % name)
    return None

  @contextlib.contextmanager
  def __DescriptorEnv(self, message_descriptor):
    # TODO(craigcitro): Typecheck?
    previous_env = self.__current_env
    self.__current_path.append(message_descriptor.name)
    self.__current_env = message_descriptor
    yield
    self.__current_path.pop()
    self.__current_env = previous_env

  def AddEnumDescriptor(self, name, description,
                        enum_values, enum_descriptions):
    """Add a new EnumDescriptor named name with the given enum values."""
    message = extended_descriptor.ExtendedEnumDescriptor()
    message.name = self.__names.ClassName(name)
    message.description = description
    self.__DeclareDescriptor(message.name)
    for index, (enum_name, enum_description) in enumerate(
        zip(enum_values, enum_descriptions)):
      enum_value = extended_descriptor.ExtendedEnumValueDescriptor()
      enum_value.name = self.__names.NormalizeEnumName(enum_name)
      enum_value.number = index
      enum_value.description = enum_description or '<no description>'
      message.values.append(enum_value)
    self.__RegisterDescriptor(message)

  def AddDescriptorFromSchema(self, schema_name, schema):
    """Add a new MessageDescriptor named schema_name based on schema."""
    # TODO(craigcitro): Is schema_name redundant?
    if self.__GetDescriptor(schema_name):
      return
    if schema.get('type') not in ('object', 'any'):
      raise ValueError(
          'Cannot create message descriptors for type %s', schema.get('type'))
    message = extended_descriptor.ExtendedMessageDescriptor()
    message.name = self.__names.ClassName(schema['id'])
    message.description = schema.get(
        'description', 'A %s object.' % message.name)
    self.__DeclareDescriptor(message.name)
    with self.__DescriptorEnv(message):
      properties = schema.get('properties', {})
      for index, (name, attrs) in enumerate(sorted(properties.iteritems())):
        field = self.__FieldDescriptorFromProperties(name, index + 1, attrs)
        message.fields.append(field)
      if 'additionalProperties' in schema:
        additional_properties_info = schema['additionalProperties']
        entries_type_name = self.__AddAdditionalPropertyType(
            message.name, additional_properties_info)
        description = additional_properties_info.get('description')
        if description is None:
          description = 'Additional properties of type %s' % message.name
        attrs = {
            'items': {
                '$ref': entries_type_name,
                },
            'description': description,
            'type': 'array',
            }
        field_name = 'additionalProperties'
        message.fields.append(self.__FieldDescriptorFromProperties(
            field_name, len(properties) + 1, attrs))
        self.__AddImport(
            'from %s import encoding' % self.__base_files_package)
        message.decorators.append(
            'encoding.MapUnrecognizedFields(%r)' % field_name)
    self.__RegisterDescriptor(message)

  def __AddAdditionalPropertyType(self, name, property_schema):
    new_type_name = 'AdditionalProperty'
    property_schema = dict(property_schema)
    # We drop the description here on purpose, so the resulting
    # messages are less repetitive.
    property_schema.pop('description', None)
    description = 'An additional property for a %s object.' % name
    schema = {
        'id': new_type_name,
        'type': 'object',
        'description': description,
        'properties': {
            'key': {
                'type': 'string',
                'description': 'Name of the additional property.',
                },
            'value': property_schema,
            },
        }
    self.AddDescriptorFromSchema(new_type_name, schema)
    return new_type_name

  def __FieldDescriptorFromProperties(self, name, index, attrs):
    field = descriptor.FieldDescriptor()
    field.name = self.__names.CleanName(name)
    field.number = index
    field.label = self.__ComputeLabel(attrs)
    new_type_name_hint = self.__names.ClassName(
        '%sValue' % self.__names.ClassName(name))
    type_info = self.__GetTypeInfo(attrs, new_type_name_hint)
    field.type_name = type_info.type_name
    field.variant = type_info.variant
    if 'default' in attrs:
      # TODO(craigcitro): Correctly handle non-primitive default values.
      default = attrs['default']
      if field.type_name != 'string' and field.variant != messages.Variant.ENUM:
        default = str(json.loads(default))
      if field.variant == messages.Variant.ENUM:
        default = self.__names.NormalizeEnumName(default)
      field.default_value = default
    extended_field = extended_descriptor.ExtendedFieldDescriptor()
    extended_field.name = field.name
    extended_field.description = attrs.get('description', 'A %s attribute.' % (
        field.type_name,))
    extended_field.field_descriptor = field
    return extended_field

  @staticmethod
  def __ComputeLabel(attrs):
    if attrs.get('required', False):
      return descriptor.FieldDescriptor.Label.REQUIRED
    elif attrs.get('type') == 'array':
      return descriptor.FieldDescriptor.Label.REPEATED
    return descriptor.FieldDescriptor.Label.OPTIONAL

  def __GetTypeInfo(self, attrs, name_hint):
    """Return a TypeInfo object for attrs, creating one if needed."""

    def AddIfUnknown(type_name):
      type_name = self.__names.ClassName(type_name)
      full_type_name = self.__ComputeFullName(type_name)
      if (full_type_name not in self.__message_registry.viewkeys() and
          type_name not in self.__message_registry.viewkeys()):
        self.__unknown_types.add(type_name)

    type_ref = self.__names.ClassName(attrs.get('$ref'))
    type_name = attrs.get('type')
    if not (type_ref or type_name):
      raise ValueError('No type found for %s' % attrs)

    if type_ref:
      AddIfUnknown(type_ref)
      return TypeInfo(type_name=type_ref, variant=messages.Variant.MESSAGE)

    if 'enum' in attrs:
      enum_name = '%sValuesEnum' % name_hint
      description = attrs.get('description', '')
      self.AddEnumDescriptor(enum_name, description,
                             attrs['enum'], attrs['enumDescriptions'])
      AddIfUnknown(enum_name)
      return TypeInfo(type_name=enum_name, variant=messages.Variant.ENUM)

    if 'format' in attrs:
      type_info = self.PRIMITIVE_FORMAT_MAP.get(attrs['format'])
      if (type_info.type_name.startswith('protorpc.message_types.') or
          type_info.type_name.startswith('message_types.')):
        self.__AddImport('from protorpc import message_types')
      if type_info is None:
        raise ValueError('Unknown format %s for type %s' % (
            attrs['format'], type_name))
      return type_info

    if type_name in self.PRIMITIVE_TYPE_INFO_MAP:
      type_info = self.PRIMITIVE_TYPE_INFO_MAP[type_name]
      return type_info

    if type_name == 'array':
      items = attrs.get('items')
      if not items:
        raise ValueError('Array type with no item type: %s' % attrs)
      item_name_hint = items.get('title') or '%sListEntry' % name_hint
      item_name_hint = self.__names.ClassName(item_name_hint)
      return self.__GetTypeInfo(items, item_name_hint)
    elif type_name == 'any':
      return self.PRIMITIVE_TYPE_INFO_MAP['string']
    elif type_name == 'object':
      # TODO(craigcitro): Think of a better way to come up with names.
      if not name_hint:
        raise ValueError('Cannot create subtype without some name hint')
      schema = dict(attrs)
      schema['id'] = name_hint
      self.AddDescriptorFromSchema(name_hint, schema)
      AddIfUnknown(name_hint)
      return TypeInfo(type_name=name_hint, variant=messages.Variant.MESSAGE)

    raise ValueError('Unknown type: %s' % type_name)
