import logging

from google.appengine.api import search as search_api

from query import SearchQuery, construct_document
from fields import TextField, IntegerField, FloatField, DateField, Field, BooleanField


class Options(object):
    """Similar to Django's Options class, holds metadata about a class with
    `__metaclass__ = MetaClass`.
    """
    def __init__(self, fields):
        self.fields = fields


class RangeDocument(object):

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class MetaClass(type):
    def __new__(cls, name, bases, dct):
        """Allows the typical declarative class pattern:

        >>> class Thing(search.Document):
        ...     prop = search.Field()
        ...
        >>> t = Thing()
        >>> t.prop = 'hello'
        >>> t.prop
        'Hello'
        >>> Thing.prop
        Traceback ...:
            ...
        AttributeError: type object 'Thing' has no attribute 'prop'
        >>> Thing._meta.fields['prop']
        <search.Field object at 0xXXXXXXXX>
        """
        new_cls = super(MetaClass, cls).__new__(cls, name, bases, dct)

        fields = {}

        # Custom inheritance -- delicious _and_ necessary!
        try:
            parents = [b for b in bases if issubclass (b, DocumentModel)]

            # Reversing simulates the usual MRO
            parents.reverse()

            for p in parents:
                parent_fields = getattr(getattr(p, '_meta', None), 'fields', None)

                if parent_fields:
                    fields.update(parent_fields)
        except NameError:
            pass

        # If there are any search fields defined on the class, allow them to
        # to set themselves up, given that we now know the name of the field
        # instance
        for name, field in dct.items():
            if isinstance(field, Field):
                field.add_to_class(new_cls, name)
                fields[name] = field
                delattr(new_cls, name)

        new_cls._meta = Options(fields)
        return new_cls


class DocumentModel(object):
    """Base class for documents added to search indexes"""

    __metaclass__ = MetaClass

    def __init__(self, **kwargs):
        # Don't bother to do any fancy Django `*args` mangling, just
        # use `**kwargs`
        for name, field in self._meta.fields.items():
            val = kwargs.pop(name, None)
            setattr(self, name, val)

        self.doc_id = unicode(kwargs.get('doc_id', '')).encode('utf-8') or None

    def __getattribute__(self, name):
        """Make sure that any attribute accessed on document classes return
        the python representation of their value.
        """
        # XXX: is this the best place to do this? Should `fields.Field`
        #      subclasses be descriptors instead?

        # Avoid recursion by looking calling `__getattribute` on the `object`
        # class with self as the instance
        val = object.__getattribute__(self, name)
        meta = object.__getattribute__(self, '_meta')
        if name in meta.fields:
            f = meta.fields[name]
            val = f.to_python(val)
        return val

    def __setattr__(self, name, val):
        """Make sure that any attibutes set on document class instances get the
        value converted to the search API accepted value.
        """
        if name in self._meta.fields:
            f = self._meta.fields[name]
            val = f.to_search_value(val)
        super(DocumentModel, self).__setattr__(name, val)


class Index(object):
    """A search index. Provides methods for adding, removing and searching
    documents in this index.
    """

    FIELD_MAP = {
        TextField: search_api.TextField,
        IntegerField: search_api.NumberField,
        FloatField: search_api.NumberField,
        DateField: search_api.DateField,
        BooleanField: search_api.AtomField,
    }

    def __init__(self, name=None):
        assert name, 'An index must have a non empty name'
        assert not name.startswith('!') and ' ' not in name,\
            "An index name must not start with a '!' and must not contain spaces"
        self.name = name
        # The actual index object from the search API
        self._index = search_api.Index(name=name)

    def list_documents(self, **kwargs):
        """Deprecated. Use get_range instead"""
        return self.get_range(**kwargs)

    def add(self, documents):
        """Deprecated in SDK 1.7.4 and will removed in 1.7.5, use `put` instead
        """
        return self.put(documents)

    def get_range(self, document_class=None, **kwargs):
        ids_only = kwargs.get("ids_only")

        if not ids_only and document_class:
            raise ValueError(
                "If ids_only is False, you must pass a document_class to "
                "instantiate with the results"
            )

        docs = self._index.get_range(**kwargs)

        if ids_only:
            return [RangeDocument(doc_id=doc.doc_id) for doc in docs]
        return [construct_document(document_class, doc) for doc in docs]

    def put(self, documents):
        """Add `documents` to this index"""

        def get_fields(d):
            """Convenience function for getting the search API fields list
            from the given document `d`.
            """
            return [self.FIELD_MAP[f.__class__](
                name=n, value=f.to_search_value(getattr(d, n, None))
                ) for n, f in d._meta.fields.items()]

        # If documents is actually just a single document, stick it in a list
        try:
            len(documents)
        except TypeError:
            documents = [documents]

        # Construct the actual search API documents to add to the underlying
        # search API index
        search_docs = []
        for d in documents:
            search_doc = search_api.Document(doc_id=d.doc_id, fields=get_fields(d))
            search_docs.append(search_doc)

        return self._index.put(search_docs)

    def remove(self, doc_ids):
        """ Deprecated in SDK 1.7.4 and will removed in 1.7.5 , use `delete` instead 
        """
        return self.delete(doc_ids)

    def delete(self, doc_ids):
        """Straight up proxy to the underlying index's `remove` method"""
        return self._index.delete(doc_ids)

    def purge(self):
        """Deletes all documents from this index.
        
        Mainly only for testing/debugging, use your own method of deleting all
        documents if you want to do so.
        """
        docs = self.get_range(ids_only=True)
        while docs:
            self.delete([d.doc_id for d in docs])
            docs = self.get_range(
                ids_only=True,
                start_id=docs[-1],
                include_start_object=False
            )

    def search(self, document_class, ids_only=False):
        """Initialise the search query for this index and document class"""
        return SearchQuery(
            self._index,
            document_class=document_class,
            ids_only=ids_only
        )
