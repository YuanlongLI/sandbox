from ..algorithm import Algorithm, AuthenticatedSymmetricEncryptionAlgorithm
from ..transform import AuthenticatedCryptoTransform, BlockCryptoTransform
from abc import abstractmethod
import codecs
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding, hashes, hmac


def _int_to_bytes(i):
    h = hex(i)
    if len(h) > 1 and h[0:2] == '0x':
        h = h[2:]
    # need to strip L in python 2.x
    h = h.strip('L')
    if len(h) % 2:
        h = '0' + h
    return codecs.decode(h, 'hex')


def _int_to_bigendian_8_bytes(i):
    b = _int_to_bytes(i)
    if len(b) < 8:
        b = (b'\0' * (8 - len(b))) + b
    return b


class _AesCbcHmacCryptoTransform(BlockCryptoTransform, AuthenticatedCryptoTransform):
    def __init__(self, key, iv, auth_data, auth_tag):
        self._aes_key = key[:len(key) // 2]
        self._hmac_key = key[len(key) // 2:]
        hash_algo = {
            256: hashes.SHA256(),
            384: hashes.SHA384(),
            512: hashes.SHA512()
        }[len(key) * 8]

        self._cipher = Cipher(algorithms.AES(self._aes_key), modes.CBC(iv), backend=default_backend())
        self._tag = auth_tag or bytearray()
        self._hmac = hmac.HMAC(self._hmac_key, hash_algo, backend=default_backend())
        self._auth_data_length = _int_to_bigendian_8_bytes(len(auth_data) * 8)

        # prime the hash
        self._hmac.update(auth_data)
        self._hmac.update(iv)

    def tag(self):
        return self._tag

    def block_size(self):
        return self._cipher.block_size

    @abstractmethod
    def update(self, data):
        raise NotImplementedError()

    @abstractmethod
    def finalize(self):
        raise NotImplementedError()

    def transform(self, data):
        return self.update(data) + self.finalize()


class _AesCbcHmacEncryptor(_AesCbcHmacCryptoTransform):
    def __init__(self, key, iv, auth_data, auth_tag):
        super(_AesCbcHmacEncryptor, self).__init__(key, iv, auth_data, auth_tag)
        self._ctx = self._cipher.encryptor()
        self._padder = padding.PKCS7(self.block_size).padder()
        self._tag[:] = []

    def update(self, data):
        padded = self._padder.update(data)
        cipher_text = self._ctx.update(padded)
        self._hmac.update(cipher_text)
        return cipher_text

    def finalize(self):
        padded = self._padder.finalize()
        cipher_text = self._ctx.update(padded) + self._ctx.finalize()
        self._hmac.update(cipher_text)
        self._hmac.update(self._auth_data_length)
        self._tag.extend(hmac.finalize()[:len(self._hmac_key)])
        return cipher_text


class _AesCbcHmacDecryptor(_AesCbcHmacCryptoTransform):
    def __init__(self, key, iv, auth_data, auth_tag):
        super(_AesCbcHmacDecryptor, self).__init__(key, iv, auth_data, auth_tag)
        self._ctx = self._cipher.decryptor()
        self._padder = padding.PKCS7(self.block_size).unpadder()

    def update(self, data):
        self._hmac.update(data)
        padded = self._ctx.update(data)
        return self._padder.update(padded)

    def finalize(self):
        self._hmac.update(self._auth_data_length)
        self._hmac.verify(self.tag)
        padded = self._ctx.finalize()
        return self._padder.update(padded) + self._padder.finalize()

    # override transform from the base so we can verify the entire hash before we start decrypting
    def transform(self, data):
        self._hmac.update(data)
        self._hmac.update(self._auth_data_length)
        self._hmac.verify(self.tag)
        padded = self._ctx.update(data) + self._ctx.finalize()
        return self._padder.update(padded) + self._padder.finalize()


class _AesCbcHmac(AuthenticatedSymmetricEncryptionAlgorithm):
    _key_size = 256

    @property
    def block_size(self):
        return self._key_size // 2

    @property
    def block_size_in_bytes(self):
        return self.block_size >> 3

    @property
    def key_size(self):
        return self._key_size

    @property
    def key_size_in_bytes(self):
        return self._key_size >> 3

    def create_encryptor(self, key, iv, auth_data, auth_tag=None):
        return _AesCbcHmacEncryptor(key, iv, auth_data, auth_tag)

    def create_decryptor(self, key, iv, auth_data, auth_tag):
        return _AesCbcHmacDecryptor(key, iv, auth_data, auth_tag)


class Aes128CbcHmacSha256(_AesCbcHmac):
    _key_size=256
    _name='A128CBC-HS256'


class Aes192CbcHmacSha384(_AesCbcHmac):
    _key_size=384
    _name='A192CBC-HS384'


class Aes256CbcHmacSha512(_AesCbcHmac):
    _key_size=512
    _name='A256CBC-HS512'


Aes128CbcHmacSha256.register()
Aes192CbcHmacSha384.register()
Aes256CbcHmacSha512.register()
