# Adapted from the DJango Rest Framework repository (https://github.com/encode/django-rest-framework)

"""
The Request class is used as a wrapper around the standard request object.

The wrapped request then offers a richer API, in particular :

    - content automatically parsed according to `Content-Type` header,
      and available as `request.data`
    - full support of PUT method, including support for file uploads
    - form overloading of HTTP method, content type and content
"""
from typing import Any, Optional
import crosshair
import icontract
import io
import sys
from contextlib import contextmanager

# Monkey-patching our fake settings over Django's settings (must be done before importing Django)
import dataset.request.utils.fake_settings

from django.conf import settings
from django.http import HttpRequest, QueryDict
from django.http.request import RawPostDataException
from django.utils.datastructures import MultiValueDict
from django.utils.http import parse_header_parameters

import dataset.request.utils.exceptions as exceptions
from dataset.request.utils.settings import api_settings

def is_form_media_type(media_type):
    """
    Return True if the media type is a valid form media type.
    """
    base_media_type, params = parse_header_parameters(media_type)
    return (base_media_type == 'application/x-www-form-urlencoded' or
            base_media_type == 'multipart/form-data')

class override_method: # pragma: no cover
    """
    A context manager that temporarily overrides the method on a request,
    additionally setting the `view.request` attribute.

    Usage:

        with override_method(view, request, 'POST') as request:
            ... # Do stuff with `view` and `request`
    """

    def __init__(self, view, request, method):
        self.view = view
        self.request = request
        self.method = method
        self.action = getattr(view, 'action', None)

    def __enter__(self):
        self.view.request = clone_request(self.request, self.method)
        # For viewsets we also set the `.action` attribute.
        action_map = getattr(self.view, 'action_map', {})
        self.view.action = action_map.get(self.method.lower())
        return self.view.request

    def __exit__(self, *args, **kwarg):
        self.view.request = self.request
        self.view.action = self.action

class WrappedAttributeError(Exception): # pragma: no cover
    pass

@contextmanager
def wrap_attributeerrors(): # pragma: no cover
    """
    Used to re-raise AttributeErrors caught during authentication, preventing
    these errors from otherwise being handled by the attribute access protocol.
    """
    try:
        yield
    except AttributeError:
        info = sys.exc_info()
        exc = WrappedAttributeError(str(info[1]))
        raise exc.with_traceback(info[2])

class Empty: # pragma: no cover
    """
    Placeholder for unset attributes.
    Cannot use `None`, as that may be a valid value.
    """
    pass

def _hasattr(obj, name): # pragma: no cover
    return not getattr(obj, name) is Empty

def clone_request(request, method): # pragma: no cover
    """
    Internal helper method to clone a request, replacing with a different
    HTTP method.  Used for checking permissions against other methods.
    """
    ret = Request(request=request._request,
                  parsers=request.parsers,
                  authenticators=request.authenticators,
                  negotiator=request.negotiator,
                  parser_context=request.parser_context)
    ret._data = request._data
    ret._files = request._files
    ret._full_data = request._full_data
    ret._content_type = request._content_type
    ret._stream = request._stream
    ret.method = method
    if hasattr(request, '_user'):
        ret._user = request._user
    if hasattr(request, '_auth'):
        ret._auth = request._auth
    if hasattr(request, '_authenticator'):
        ret._authenticator = request._authenticator
    if hasattr(request, 'accepted_renderer'):
        ret.accepted_renderer = request.accepted_renderer
    if hasattr(request, 'accepted_media_type'):
        ret.accepted_media_type = request.accepted_media_type
    if hasattr(request, 'version'):
        ret.version = request.version
    if hasattr(request, 'versioning_scheme'):
        ret.versioning_scheme = request.versioning_scheme
    return ret

class ForcedAuthentication: # pragma: no cover
    """
    This authentication class is used if the test client or request factory
    forcibly authenticated the request.
    """

    def __init__(self, force_user, force_token):
        self.force_user = force_user
        self.force_token = force_token

    def authenticate(self, request):
        return (self.force_user, self.force_token)


@icontract.invariant(
    lambda self: "encoding" in self.parser_context and bool(self.parser_context["encoding"]),
    "parser_context must contain a non-empty 'encoding' entry."
)
@icontract.invariant(
    lambda self: self._data is Empty or self._full_data is not Empty,
    "If _data has been loaded, then _full_data must also be set."
)
@icontract.invariant(
    lambda self: not hasattr(self, "_authenticator") or (self._authenticator is None or (hasattr(self, "_user") and bool(self._user))),
    "If an authenticator is set, then _user must be a truthy value."
)
@icontract.invariant(
    lambda self: self._stream is Empty or self._stream is None or self._stream is self._request or hasattr(self._stream, "read"),
    "_stream must be either not loaded, the original request, or a stream-like object."
)
@icontract.invariant(
    lambda self: self._data is Empty or self._full_data is not Empty,
    "If _data has been loaded, then _full_data must also be set."
)
class Request:
    """
    Wrapper allowing to enhance a standard `HttpRequest` instance.

    inv: "encoding" in self.parser_context and bool(self.parser_context["encoding"])
    inv: self._data is Empty or self._full_data is not Empty
    inv: not hasattr(self, "_authenticator") or (self._authenticator is None or (hasattr(self, "_user") and bool(self._user)))
    inv: self._stream is Empty or self._stream is None or self._stream is self._request or hasattr(self._stream, "read")
    inv: self._data is Empty or self._full_data is not Empty
    """

    # CrossHair cannot handle this precondition, and as such the parser_context parameter has been removed.
    # @icontract.require(
    #     lambda parser_context: (parser_context is None) or ("encoding" in parser_context and bool(parser_context["encoding"])),
    #     "If provided, parser_context must include a non-empty 'encoding' key."
    # )
    @icontract.ensure(
        lambda self: "encoding" in self.parser_context and bool(self.parser_context["encoding"]),
        "parser_context contains a non-empty 'encoding' entry."
    )
    def __init__(self, 
                 request: HttpRequest, 
                 parsers: Optional[list] = None, 
                 authenticators: Optional[list] = None):
        """
        Initialize a new Request instance that wraps a standard HttpRequest and enhances it.

        Parameters:
        - request (HttpRequest): The original Django HttpRequest.
        - parsers (list, optional): A list of parsers to process the request content.
        - authenticators (list, optional): A list of authenticators for user authentication.

        Postconditions:
        - The instance's parser_context will contain a non-empty 'encoding' and a reference to this Request instance.
        """
        
        self._request = request
        self.parsers = () if parsers is None else parsers
        self.authenticators = () if authenticators is None else authenticators
        self.negotiator = self._default_negotiator()
        self.parser_context = None
        self._data = Empty
        self._files = Empty
        self._full_data = Empty
        self._content_type = Empty
        self._stream = Empty

        if self.parser_context is None:
            self.parser_context = {}
        self.parser_context['request'] = self
        self.parser_context['encoding'] = request.encoding or settings.DEFAULT_CHARSET

        force_user = getattr(request, '_force_auth_user', None)
        force_token = getattr(request, '_force_auth_token', None)
        if force_user is not None or force_token is not None:
            forced_auth = ForcedAuthentication(force_user, force_token)
            self.authenticators = (forced_auth,)

    def _default_negotiator(self):
        return api_settings.DEFAULT_CONTENT_NEGOTIATION_CLASS()

    @property
    def content_type(self) -> str: # pragma: no cover
        """
        Retrieve the content type from the underlying request's META information.

        Returns:
        - str: The content type string.

        Postconditions:
        - The returned content type is not None.
        """
        meta = self._request.META
        return meta.get('CONTENT_TYPE', meta.get('HTTP_CONTENT_TYPE', ''))

    @property
    def stream(self) -> Optional[HttpRequest | io.BytesIO]: # pragma: no cover
        """
        Return a stream-like object representing the request content.

        Returns:
        - Optional[Union[HttpRequest, io.BytesIO]]: Either the original request (if unread), a stream-like object with a 'read' method, or None if there is no content.
        """
        if not _hasattr(self, '_stream'):
            self._load_stream()
        return self._stream

    @property
    def query_params(self) -> QueryDict: # pragma: no cover
        """
        Retrieve the query parameters from the underlying request.

        Returns:
        - QueryDict: The GET parameters of the underlying HttpRequest.
        """
        return self._request.GET

    @property
    def data(self): # pragma: no cover
        """
        Return the parsed data from the request content.

        Returns:
        - Any: The combined data parsed from the request (including form data and file uploads).
        """
        if not _hasattr(self, '_full_data'):
            with wrap_attributeerrors():
                self._load_data_and_files()
        return self._full_data

    @property
    def user(self): # pragma: no cover
        """
        Return the authenticated user associated with the request.

        Returns:
        - Any: The user object if authentication was successful, or None otherwise.
        """
        if not hasattr(self, '_user'):
            with wrap_attributeerrors():
                self._authenticate()
        return self._user

    @user.setter
    def user(self, value) -> None:
        """
        Sets the user on the current request. This is necessary to maintain
        compatibility with django.contrib.auth where the user property is
        set in the login and logout functions.

        Note that we also set the user on Django's underlying `HttpRequest`
        instance, ensuring that it is available to any middleware in the stack.

        Parameters:
        - value (Any): The user object to be associated with the request.
        """
        self._user = value
        self._request.user = value

    @property
    def auth(self): # pragma: no cover
        """
        Returns any non-user authentication information associated with the
        request, such as an authentication token.

        Returns:
        - Any: The authentication details.
        """
        if not hasattr(self, '_auth'):
            with wrap_attributeerrors():
                self._authenticate()
        return self._auth

    @auth.setter
    def auth(self, value) -> None:
        """
        Sets any non-user authentication information associated with the
        request, such as an authentication token.

        Parameters:
        - value (Any): The authentication token or details.
        """
        self._auth = value
        self._request.auth = value

    @property
    def successful_authenticator(self): # pragma: no cover
        """
        Return the instance of the authentication instance class that was used
        to authenticate the request, or `None`.

        Returns:
        - Any: The successful authenticator, or None if authentication was not successful.
        """
        if not hasattr(self, '_authenticator'):
            with wrap_attributeerrors():
                self._authenticate()
        return self._authenticator

    @icontract.ensure(
        lambda self: self._data is not Empty, 
        "_data must be set after _load_data_and_files"
    )
    @icontract.ensure(
        lambda self: self._full_data is not Empty, 
        "_full_data must be set after _load_data_and_files"
    )
    def _load_data_and_files(self) -> None:
        """
        Parse the request content and load both data and files.

        Postconditions:
        - _data is updated to contain the parsed data.
        - _full_data is set, combining _data and _files.
        """
        if not _hasattr(self, '_data'):
            self._data, self._files = self._parse()
            if self._files:
                self._full_data = self._data.copy()
                self._full_data.update(self._files)
            else:
                self._full_data = self._data

            # if a form media type, copy data & files refs to the underlying
            # http request so that closable objects are handled appropriately.
            if is_form_media_type(self.content_type):
                self._request._post = self.POST
                self._request._files = self.FILES

    @icontract.ensure(
        lambda self: self._stream is None 
                     or self._stream == self._request 
                     or hasattr(self._stream, "read"),
        "After _load_stream, _stream must be either None, the original request, or a stream-like object."
    )
    def _load_stream(self) -> None:
        """
        Load the request content as a stream for further processing.

        Returns:
        - None

        Postconditions:
        - _stream is set to either None, the original request, or a stream-like object with a 'read' method.
        """
        meta = self._request.META
        try:
            content_length = int(
                meta.get('CONTENT_LENGTH', meta.get('HTTP_CONTENT_LENGTH', 0))
            )
        except (ValueError, TypeError):
            content_length = 0

        if content_length == 0:
            self._stream = None
        elif not self._request._read_started:
            self._stream = self._request
        else:
            self._stream = io.BytesIO(self.body)

    def _supports_form_parsing(self) -> bool:
        """
        Determine if the request supports form parsing.

        Returns:
        - bool: True if at least one parser supports form media types; False otherwise.
        """
        form_media = (
            'application/x-www-form-urlencoded',
            'multipart/form-data'
        )
        return any(parser.media_type in form_media for parser in self.parsers)

    def _parse(self) -> tuple[Any, Any]: # pragma: no cover
        """
        Parse the request content, returning a two-tuple of (data, files)

        May raise an `UnsupportedMediaType`, or `ParseError` exception.

        Returns:
        - tuple[Any, Any]: A two-tuple where the first element is the parsed data and the second is the parsed files.
        """
        media_type = self.content_type
        try:
            stream = self.stream
        except RawPostDataException:
            if not hasattr(self._request, '_post'):
                raise
            # If request.POST has been accessed in middleware, and a method='POST'
            # request was made with 'multipart/form-data', then the request stream
            # will already have been exhausted.
            if self._supports_form_parsing():
                return (self._request.POST, self._request.FILES)
            stream = None

        if stream is None or media_type is None:
            if media_type and is_form_media_type(media_type):
                empty_data = QueryDict('', encoding=self._request._encoding)
            else:
                empty_data = {}
            empty_files = MultiValueDict()
            return (empty_data, empty_files)

        parser = self.negotiator.select_parser(self, self.parsers)

        if not parser:
            raise exceptions.UnsupportedMediaType(media_type)

        try:
            parsed = parser.parse(stream, media_type, self.parser_context)
        except Exception:
            # If we get an exception during parsing, fill in empty data and
            # re-raise.  Ensures we don't simply repeat the error when
            # attempting to render the browsable renderer response, or when
            # logging the request or similar.
            self._data = QueryDict('', encoding=self._request._encoding)
            self._files = MultiValueDict()
            self._full_data = self._data
            raise

        # Parser classes may return the raw data, or a
        # DataAndFiles object.  Unpack the result as required.
        try:
            return (parsed.data, parsed.files)
        except AttributeError:
            empty_files = MultiValueDict()
            return (parsed, empty_files)

    def _authenticate(self) -> None: # pragma: no cover
        """
        Attempt to authenticate the request using each authentication instance
        in turn.
        """
        for authenticator in self.authenticators:
            try:
                user_auth_tuple = authenticator.authenticate(self)
            except exceptions.APIException:
                self._not_authenticated()
                raise

            if user_auth_tuple is not None:
                self._authenticator = authenticator
                self.user, self.auth = user_auth_tuple
                return

        self._not_authenticated()

    @icontract.ensure(
        lambda self: self._authenticator is None, 
        "After _not_authenticated, _authenticator must be None"
    )
    def _not_authenticated(self) -> None:
        """
        Set authenticator, user & authtoken representing an unauthenticated request.

        Defaults are None, AnonymousUser & None.

        Postconditions:
        - _authenticator is set to None.
        """
        self._authenticator = None

        if api_settings.UNAUTHENTICATED_USER:
            self.user = api_settings.UNAUTHENTICATED_USER()
        else:
            self.user = None

        if api_settings.UNAUTHENTICATED_TOKEN:
            self.auth = api_settings.UNAUTHENTICATED_TOKEN()
        else:
            self.auth = None

    def __getattr__(self, attr: str):
        """
        Retrieve an attribute from the underlying request if it is not found on this instance.

        Parameters:
        - attr (str): The name of the attribute to retrieve.

        Returns:
        - Any: The attribute value from the underlying HttpRequest.
        """
        try:
            _request = self.__getattribute__("_request")
            return getattr(_request, attr)
        except AttributeError:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{attr}'")

    @property
    def POST(self) -> QueryDict: # pragma: no cover
        """
        Return the POST data of the request.

        Returns:
        - QueryDict: The POST data, either parsed from the request if the content type is form media, or an empty QueryDict.
        """
        # Ensure that request.POST uses our request parsing.
        if not _hasattr(self, '_data'):
            with wrap_attributeerrors():
                self._load_data_and_files()
        if is_form_media_type(self.content_type):
            return self._data
        return QueryDict('', encoding=self._request._encoding)

    @property
    def FILES(self) -> MultiValueDict: # pragma: no cover
        """
        Return the FILES data of the request.

        Returns:
        - MultiValueDict: The files uploaded as part of the request.
        """
        # Leave this one alone for backwards compat with Django's request.FILES
        # Different from the other two cases, which are not valid property
        # names on the WSGIRequest class.
        if not _hasattr(self, '_files'):
            with wrap_attributeerrors():
                self._load_data_and_files()
        return self._files

    @icontract.ensure(
        lambda self, value: self._request.is_ajax() == value, 
        "force_plaintext_errors must set is_ajax() to the forced value"
    )
    def force_plaintext_errors(self, value: bool) -> None:
        # Hack to allow our exception handler to force choice of
        # plaintext or html error responses.
        self._request.is_ajax = lambda: value

class FakeSymbolicHttpRequest: # pragma: no cover
    META: dict
    user = None
    auth = None
    _read_started = False
    encoding = "utf-8"
    method = "GET"
    content_type = "application/json"
    body = b'{"key":"value"}'

    def __init__(self):
        self._read_started = False
        self.encoding = "utf-8"
        self.method = "GET"
        self.content_type = "application/json"
        self.body = b'{"key":"value"}'
        self.META = {}

def symbolic_request(factory: crosshair.SymbolicFactory) -> Request: # pragma: no cover
    req = FakeSymbolicHttpRequest()
    return Request(req, factory(list), factory(list))

crosshair.register_type(Request, symbolic_request) # pragma: no cover