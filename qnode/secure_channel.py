import os
import json
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

# AES-GCM usa um Nonce (Number used once) de 12 bytes
NONCE_BYTES = 12
# AES-GCM adiciona um 'Tag' de 16 bytes para autenticação
TAG_BYTES = 16

def encrypt_aes_gcm(key_hex: str, data: dict) -> dict:
    """
    Cifra dados usando AES-GCM com a chave fornecida.
    Retorna um dicionário com nonce e ciphertext (ambos em hex).
    """
    try:
        key_bytes = bytes.fromhex(key_hex)
        aesgcm = AESGCM(key_bytes)
        
        # 1. Gerar um nonce aleatório
        nonce_bytes = os.urandom(NONCE_BYTES)
        
        # 2. Converter dados (dict) para bytes (JSON)
        plaintext_bytes = json.dumps(data).encode('utf-8')
        
        # 3. Cifrar
        # (O 'tag' de autenticação é automaticamente anexado ao final do ciphertext)
        ciphertext_bytes = aesgcm.encrypt(nonce_bytes, plaintext_bytes, None)
        
        return {
            "nonce_hex": nonce_bytes.hex(),
            "ciphertext_hex": ciphertext_bytes.hex()
        }
    except Exception as e:
        print(f"[Crypto] Erro ao cifrar: {e}")
        raise

def decrypt_aes_gcm(key_hex: str, nonce_hex: str, ciphertext_hex: str) -> dict:
    """
    Decifra dados AES-GCM.
    Levanta 'InvalidTag' se a chave ou os dados estiverem errados/corrompidos.
    Retorna o dicionário de dados original.
    """
    try:
        key_bytes = bytes.fromhex(key_hex)
        nonce_bytes = bytes.fromhex(nonce_hex)
        ciphertext_bytes = bytes.fromhex(ciphertext_hex)
        
        aesgcm = AESGCM(key_bytes)
        
        # 1. Decifrar (e verificar o tag de autenticação)
        #    Isso falhará com 'InvalidTag' se a chave estiver errada ou
        #    a mensagem tiver sido adulterada.
        plaintext_bytes = aesgcm.decrypt(nonce_bytes, ciphertext_bytes, None)
        
        # 2. Converter bytes (JSON) de volta para dados (dict)
        data = json.loads(plaintext_bytes.decode('utf-8'))
        return data
        
    except InvalidTag:
        print("[Crypto] ERRO: Falha na verificação do TAG. Mensagem adulterada ou chave errada.")
        raise
    except Exception as e:
        print(f"[Crypto] Erro ao decifrar: {e}")
        raise