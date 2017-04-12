#!/usr/local/bin/python
from azure.common.credentials import ServicePrincipalCredentials
from azure.common.credentials import UserPassCredentials
from azure.mgmt.keyvault.models import Sku
from azure.mgmt.keyvault.models import VaultCreateOrUpdateParameters, VaultProperties, SkuName, AccessPolicyEntry, \
    Permissions, KeyPermissions, SecretPermissions, CertificatePermissions
from azure.common.credentials import BasicTokenAuthentication
from azure.keyvault import KeyVaultClient
from azure.mgmt.keyvault import KeyVaultManagementClient
from adal import token_cache
import adal
import json
import os
import sys


CLIENT_ID = '8fd4d3c4-efea-49aa-b1de-2c33c22da56e' # Azure cli
CLIENT_OID = '8694d835-b4e2-419a-a315-b13c854166e2'
CLIENT_TENANT_ID = 'a7fc734e-9961-43ce-b4de-21b8b38403ba'
KEY_VAULT_RESOURCE = 'https://vault.azure.net'
AZURE_MANAGEMENT_RESOURCE2 = 'https://management.core.windows.net/'

def _json_format(obj):
    return json.dumps(obj, sort_keys=True, indent=4, separators=(',', ': '))

class KV_Config(dict):
    def __init__(self):

        self.authority_url = ''
        self.subscription_id = ''
        self.tenant_id = ''
        self.token_cache = ''
        self.user_id = ''
        self.resource_group = ''
        self.user_oid = ''
        self.location = 'westus'

    def __getattr__(self, item):
        return self[item]

    def __setattr__(self, key, value):
        self[key] = value

    def from_disk(self):
        if os.path.isfile('kvconfig.json'):
            with open('kvconfig.json', 'r') as configFile:
                try:
                    dict = json.load(configFile)
                except json.JSONDecodeError:
                    print('error loading config file')
                    return
                for key, value in dict.items():
                    if value:
                        self[key] = value

    def to_disk(self):
        with open('kvconfig.json', 'w') as configFile:
            json.dump(self, configFile, sort_keys=True, indent=4, separators=(',', ': '))

class KV_Auth(object):
    def __init__(self, config):
        self._config = config

        self._cache = token_cache.TokenCache()

        if self._config.token_cache:
            self._cache.deserialize(self._config.token_cache)

        self._context = adal.AuthenticationContext(self._config.authority_url, cache=self._cache)


    def get_keyvault_creds(self):
        return self._get_creds(KEY_VAULT_RESOURCE)

    def get_arm_creds(self):
        return self._get_creds(AZURE_MANAGEMENT_RESOURCE2)

    def _get_auth_token_from_code(self, resource):
        code = self._context.acquire_user_code(resource, CLIENT_ID)

        print(code['message'])

        token = self._context.acquire_token_with_device_code(resource, code, CLIENT_ID)

        return token

    def _get_creds(self, resource):
        token = None

        if not self._config.user_id:
            token = self._get_auth_token_from_code(resource)
        else:
            token = self._context.acquire_token(resource, self._config.user_id, CLIENT_ID)

            if not token:
                token = self._get_auth_token_from_code(resource)

        self._config.token_cache = self._cache.serialize()

        if token:
            if not self._config.user_id:
                self._config.user_id = token['userId']

            if not self._config.user_oid:
                self._config.user_oid = token['oid']

            token['access_token'] = token['accessToken']

            return BasicTokenAuthentication(token)

        return None


class KV_Repl(object):

    _repl_break_commands = set(('back', 'b'))

    _repl_quit_commands = set(('quit', 'q'))

    def __init__(self, config):
        self._auth = KV_Auth(config)
        self._config = config
        self._mgmt_client = KeyVaultManagementClient(self._auth.get_arm_creds(), config.subscription_id)
        self._data_client = KeyVaultClient(self._auth.get_keyvault_creds())
        self._selected_vault = None
        self._current_index = None

    def start(self):
        try:
            self._vault_index_loop();

        except SystemExit:
            print('\nuser exited\n')

    def _continue_repl(self, display_action, break_commands=()):
        display_action()

        self._selection = input('> ').lower()

        if self._selection in break_commands:
            return None

        elif self._selection in KV_Repl._repl_quit_commands:
            sys.exit()

        try:
            self._selection = int(self._selection)
        except ValueError:
            pass

        return self._selection


    def _display_vault_index(self):

        print('\nAvailable Vaults:\n')

        self._current_index = self._get_vault_list()

        for idx, vault in enumerate(self._current_index):
            print('%d. %s' % (idx, vault.name))

        print('\n#:select | (a)dd | (d)elete | (q)uit')


    def _vault_index_loop(self):
        while self._continue_repl(self._display_vault_index) is not None:
            vaults = self._current_index

            if isinstance(self._selection, int):
                i = self._selection

                if i >= 0 and i < len(vaults):
                    self._selected_vault = self._mgmt_client.vaults.get(self._config.resource_group, vaults[i].name)
                    self._vault_detail_loop()
                else:
                    print('invalid vault index')

            elif self._selection == 'a' or self._selection == 'add':
                self._add_vault()

            else:
                print('invalid input')

    def _add_vault(self):
        name = input('\nenter vault name:')

        all_perms = Permissions()
        all_perms.keys = [KeyPermissions.all]
        all_perms.secrets = [SecretPermissions.all]
        all_perms.certificates = [CertificatePermissions.all]

        user_policy = AccessPolicyEntry(self._config.tenant_id, self._config.user_oid, all_perms)

        app_policy = AccessPolicyEntry(CLIENT_TENANT_ID, CLIENT_OID, all_perms)

        access_policies = [user_policy, app_policy]

        properties = VaultProperties(self._config.tenant_id, Sku(name='standard'), access_policies)

        properties.enabled_for_deployment = True
        properties.enabled_for_disk_encryption = True
        properties.enabled_for_template_deployment = True

        vault = VaultCreateOrUpdateParameters(self._config.location, properties)

        self._mgmt_client.vaults.create_or_update(self._config.resource_group, name, vault)

        print('vault %s created\n' % name)


    def _display_selected_vault_detail(self):
        print('\nName:\t%s' % self._selected_vault.name)
        print('Uri:\t%s' % self._selected_vault.properties.vault_uri)
        print('Id:\t%s' % self._selected_vault.id)

        print('\n(s)ecrets (k)eys (c)ertificates (b)ack (q)uit\n')

    def _vault_detail_loop(self):

        while self._continue_repl(self._display_selected_vault_detail, break_commands=KV_Repl._repl_break_commands) is not None:

            if self._selection == 's' or self._selection == 'secrets':
                self._secret_index_loop()

            elif self._selection == 'k' or self._selection == 'keys':
                print('\nnot yet implemented\n')

            elif self._selection == 'c' or self._selection == 'certificates':
                print('\nnot yet implemented\n')

            else:
                print('invalid input')

    def _display_secret_index(self):
        self._current_index = []

        secret_iter = self._data_client.get_secrets(self._selected_vault.properties.vault_uri)

        if secret_iter is not None:
            try:
                self._current_index = [secret for secret in secret_iter]
            except TypeError:
                pass

        print('\n%s Secrets:\n' % self._selected_vault.name)

        for idx, s in enumerate(self._current_index):
            print('%d. %s' % (idx, KV_Repl._get_secret_name_from_url(s.id)))

        print('\n#:show secret value (a)dd (d)elete (b)ack (q)uit\n')

    def _secret_index_loop(self):

        while self._continue_repl(self._display_secret_index, break_commands=KV_Repl._repl_break_commands) is not None:

            secrets = self._current_index

            if isinstance(self._selection, int):
                i = self._selection

                if i >= 0 and i < len(secrets):
                    print('\n%s = %s\n' % (KV_Repl._get_secret_name_from_url(secrets[i].id), self._data_client.get_secret(secrets[i].id).value))
                else:
                    print('invalid secret index')

            elif self._selection == 'a' or self._selection == 'add':
                self._add_secret()

            elif self._selection == 'd' or self._selection == 'delete':
                print('\nnot yet implemented\n')

    def _add_secret(self):
        secret_name = input('\nSecret Name: ')
        secret_value = input('Secret Value: ')
        self._data_client.set_secret(self._selected_vault.properties.vault_uri, secret_name, secret_value)
        print('\nSecret %s added to vault %s' % (secret_name, self._selected_vault.name))

    @staticmethod
    def _get_secret_name_from_url(url):
        split = url.split('/')
        return split[len(split) - 1]

    def _get_vault_list(self):
        vault_list = [vault for vault in self._mgmt_client.vaults.list()]
        return vault_list

config = KV_Config()

config.from_disk()

repl = KV_Repl(config)

repl.start()

config.to_disk()



