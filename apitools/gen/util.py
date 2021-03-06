#!/usr/bin/env python
"""Assorted utilities shared between parts of apitools."""

import collections
import contextlib
import json
import keyword
import logging
import os
import re
import urllib2
import urlparse



class Error(Exception):
  """Base error for apitools generation."""


class CommunicationError(Error):
  """Error in network communication."""


def _SortLengthFirst(a, b):
  return -cmp(len(a), len(b)) or cmp(a, b)


class Names(object):
  """Utility class for cleaning and normalizing names in a fixed style."""
  DEFAULT_NAME_CONVENTION = 'LOWER_CAMEL'
  NAME_CONVENTIONS = ['LOWER_CAMEL', 'LOWER_WITH_UNDER', 'NONE']

  def __init__(self, strip_prefixes,
               name_convention=None,
               capitalize_enums=False):
    self.__strip_prefixes = sorted(strip_prefixes, cmp=_SortLengthFirst)
    self.__name_convention = name_convention or self.DEFAULT_NAME_CONVENTION
    self.__capitalize_enums = capitalize_enums

  @staticmethod
  def __FromCamel(name, separator='_'):
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1%s\2' % separator, name)
    return name.lower()

  @staticmethod
  def __ToCamel(name, separator='_'):
    return ''.join(s[0].upper() + s[1:] for s in name.split(separator))

  @staticmethod
  def __ToLowerCamel(name, separator='_'):
    name = Names.__ToCamel(name, separator=separator)
    return name[0].lower() + name[1:]

  def __StripName(self, name):
    """Strip strip_prefix entries from name."""
    if not name:
      return name
    for prefix in self.__strip_prefixes:
      if name.startswith(prefix):
        return name[len(prefix):]
    return name

  @staticmethod
  def CleanName(name):
    """Perform generic name cleaning."""
    name = re.sub('[^_A-Za-z0-9]', '_', name)
    if name[0].isdigit():
      name = '_%s' % name
    while name in keyword.kwlist:
      name = '%s_' % name
    return name

  @staticmethod
  def NormalizeRelativePath(path):
    """Normalize camelCase entries in path."""
    path_components = path.split('/')
    normalized_components = []
    for component in path_components:
      if re.match(r'{[A-Za-z0-9_]+}$', component):
        normalized_components.append(
            '{%s}' % Names.CleanName(component[1:-1]))
      else:
        normalized_components.append(component)
    return '/'.join(normalized_components)

  def NormalizeEnumName(self, enum_name):
    if self.__capitalize_enums:
      return enum_name.upper()
    return enum_name

  def ClassName(self, name, separator='_'):
    """Generate a valid class name from name."""
    # TODO(craigcitro): Get rid of this case here and in MethodName.
    if name is None:
      return name
    # TODO(craigcitro): This is a hack to handle the case of specific
    # protorpc class names; clean this up.
    if name.startswith('protorpc.') or name.startswith('message_types.'):
      return name
    name = self.__StripName(name)
    name = self.__ToCamel(name, separator=separator)
    return self.CleanName(name)

  def MethodName(self, name, separator='_'):
    """Generate a valid method name from name."""
    if name is None:
      return None
    name = Names.__ToCamel(name, separator=separator)
    return Names.CleanName(name)

  def FieldName(self, name):
    """Generate a valid field name from name."""
    # TODO(craigcitro): We shouldn't need to strip this name, but some
    # of the service names here are excessive. Fix the API and then
    # remove this.
    name = self.__StripName(name)
    if self.__name_convention == 'LOWER_CAMEL':
      name = Names.__ToLowerCamel(name)
    elif self.__name_convention == 'LOWER_WITH_UNDER':
      name = Names.__FromCamel(name)
    return Names.CleanName(name)


@contextlib.contextmanager
def Chdir(dirname, create=True):
  if not os.path.exists(dirname):
    if not create:
      raise OSError('Cannot find directory %s' % dirname)
    else:
      os.mkdir(dirname)
  previous_directory = os.getcwd()
  os.chdir(dirname)
  yield
  os.chdir(previous_directory)


def NormalizeVersion(version):
  # Currently, '.' is the only character that might cause us trouble.
  return version.replace('.', '_')


class ClientInfo(collections.namedtuple('ClientInfo', (
    'package', 'scopes', 'version', 'client_id', 'client_secret',
    'user_agent', 'client_class_name', 'url_version', 'api_key'))):
  """Container for client-related info and names."""

  @classmethod
  def Create(cls, discovery_doc,
             scope_ls, client_id, client_secret, user_agent, names, api_key):
    """Create a new ClientInfo object from a discovery document."""
    scopes = set(
        discovery_doc.get('auth', {}).get('oauth2', {}).get('scopes', {}))
    scopes.update(scope_ls)
    client_info = {
        'package': discovery_doc['name'],
        'version': NormalizeVersion(discovery_doc['version']),
        'url_version': discovery_doc['version'],
        'scopes': sorted(list(scopes)),
        'client_id': client_id,
        'client_secret': client_secret,
        'user_agent': user_agent,
        'api_key': api_key,
        }
    client_class_name = ''.join(
        map(names.ClassName, (client_info['package'], client_info['version'])))
    client_info['client_class_name'] = client_class_name
    return cls(**client_info)

  @property
  def default_directory(self):
    return self.package

  @property
  def cli_rule_name(self):
    return '%s_%s' % (self.package, self.version)

  @property
  def cli_file_name(self):
    return '%s.py' % self.cli_rule_name

  @property
  def client_rule_name(self):
    return '%s_%s_client' % (self.package, self.version)

  @property
  def client_file_name(self):
    return '%s.py' % self.client_rule_name

  @property
  def messages_rule_name(self):
    return '%s_%s_messages' % (self.package, self.version)

  @property
  def services_rule_name(self):
    return '%s_%s_services' % (self.package, self.version)

  @property
  def messages_file_name(self):
    return '%s.py' % self.messages_rule_name

  @property
  def messages_proto_file_name(self):
    return '%s.proto' % self.messages_rule_name

  @property
  def services_proto_file_name(self):
    return '%s.proto' % self.services_rule_name


def GetPackage(path):
  path_components = path.split(os.path.sep)
  return '.'.join(path_components)


class SimplePrettyPrinter(object):
  """Simple pretty-printer that supports an indent contextmanager."""

  def __init__(self, out):
    self.__out = out
    self.__indent = ''

  @property
  def indent(self):
    return self.__indent

  def CalculateWidth(self, max_width=78):
    return max_width - len(self.indent)

  @contextlib.contextmanager
  def Indent(self, indent='  '):
    previous_indent = self.__indent
    self.__indent = '%s%s' % (previous_indent, indent)
    yield
    self.__indent = previous_indent

  def __call__(self, *args):
    if args and args[0]:
      line = (args[0] % args[1:]).rstrip()
      print >>self.__out, '%s%s' % (self.__indent, line)
    else:
      print >>self.__out, ''


def NormalizeDiscoveryUrl(discovery_url):
  """Expands a few abbreviations into full discovery urls."""
  if discovery_url.startswith('http'):
    return discovery_url
  elif '.' not in discovery_url:
    raise ValueError('Unrecognized value "%s" for discovery url')
  api_name, _, api_version = discovery_url.partition('.')
  return 'https://www.googleapis.com/discovery/v1/apis/%s/%s/rest' % (
      api_name, api_version)


def FetchDiscoveryDoc(discovery_url, retries=5):
  """Fetch the discovery document at the given url."""
  discovery_url = NormalizeDiscoveryUrl(discovery_url)
  discovery_doc = None
  last_exception = None
  for _ in xrange(retries):
    try:
      discovery_doc = json.loads(urllib2.urlopen(discovery_url).read())
      break
    except (urllib2.HTTPError, urllib2.URLError) as last_exception:
      logging.warning('Attempting to fetch discovery doc again after "%s"',
                      last_exception)
  if discovery_doc is None:
    raise CommunicationError('Could not find discovery doc at url "%s": %s' % (
        discovery_url, last_exception))
  return discovery_doc
