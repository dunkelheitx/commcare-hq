"""
couch models go here
"""
from __future__ import absolute_import
from couchdbkit.ext.django.schema import *
from couchdbkit.schema.properties_proxy import SchemaListProperty

class DjangoUser(Document):
    id = IntegerProperty()
    username = StringProperty()
    first_name = StringProperty()
    last_name = StringProperty()
    django_type = StringProperty()
    is_active = BooleanProperty()
    email = StringProperty()
    is_superuser = BooleanProperty()
    is_staff = BooleanProperty()
    last_login = DateTimeProperty()
    groups = ListProperty()
    user_permissions = ListProperty()
    password = StringProperty()
    date_joined = DateTimeProperty()
        
    class Meta:
        app_label = 'users'

class DomainAccount(Document):
    """
    Each user can have multiple accounts on the 
    web domain. This is primarily for Dimagi staff.
    """
    username = StringProperty()
    domain = StringProperty()
    permissions = StringListProperty()
    last_login = DateTimeProperty()
    date_joined = DateTimeProperty()
    
    class Meta:
        app_label = 'users'

class CommCareAccount(Document):
    """
    This is the information associated with a 
    particular commcare user. Right now, we 
    associate one commcare user to one web user
    (with multiple domain logins, phones, SIMS)
    but we could always extend to multiple commcare
    users if desired later.
    """
    uuid = StringProperty()
    username = StringProperty()
    password = StringProperty()
    date = DateTimeProperty()
    registering_phone_id = StringProperty()
    user_data = DictProperty()
    domain = StringProperty()
    
    class Meta:
        app_label = 'users'

class PhoneDevice(Document):
    """
    This is a physical device with a unique IMEI
    Note, though, that the same physical device can 
    be used with multiple SIM cards (and multiple phone numbers)
    """
    is_default = BooleanProperty()
    device_id = StringProperty()
    created = DateTimeProperty()
    
    class Meta:
        app_label = 'users'

class PhoneNumber(Document):
    """
    This is the SIM card with a unique phone number
    The same SIM card can be used in multiple phone
    devices
    """
    is_default = BooleanProperty()
    number = StringProperty()
    created = DateTimeProperty()
    
    class Meta:
        app_label = 'users'

class CouchUser(Document):
    """
    a user (for web+commcare+sms)
    can be associated with multiple usename/password/login tuples
    can be associated with multiple phone numbers/SIM cards
    can be associated with multiple phones/device IDs
    """
    UUID = StringProperty(required=True)
    django_user = SchemaProperty(DjangoUser)
    domain_accounts = SchemaListProperty(DomainAccount)
    commcare_accounts = SchemaListProperty(CommCareAccount)
    phone_devices = SchemaListProperty(PhoneDevice)
    phone_numbers = SchemaListProperty(PhoneNumber)
    created_on = DateTimeProperty()

    _user = None
    _user_checked = False

    class Meta:
        app_label = 'users'
        
    def add_domain_account(self, username, domain, **kwargs):
        self.domain_accounts.append(DomainAccount(username = username, 
                                                  domain = domain,
                                                  **kwargs))
    
    def add_commcare_account(self, username, password, domain, **kwargs):
        self.commcare_accounts.append(CommCareAccount(username=username,
                                                      password=password,
                                                      domain=domain,
                                                      **kwargs))
       
    def add_phone(self, IMEI, default=False, **kwargs):
        self.phone_devices.append(PhoneDevice(IMEI=IMEI,
                                              default=default,
                                              **kwargs))
        
    def add_phone_number(self, number, default=False, **kwargs):
        self.phone_numbers.append(PhoneNumber(number=number,
                                              default=default,
                                              **kwargs))

