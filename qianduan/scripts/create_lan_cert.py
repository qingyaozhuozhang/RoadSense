"""Generate a local LAN HTTPS certificate and certificate authority for phone geolocation.

Usage from qianduan:
    python scripts/create_lan_cert.py --ip 192.168.1.10

Install certs/ca.crt as a trusted CA on each testing phone. NEVER copy ca.key to phone!
Requires cryptography from requirements.txt.
"""
import argparse
import ipaddress
from pathlib import Path
from datetime import datetime,timedelta,timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

parser=argparse.ArgumentParser()
parser.add_argument('--ip',required=True,help='电脑真实可从手机访问的 Wi-Fi/LAN IPv4，如 192.168.1.10')
parser.add_argument('--out',default='certs',help='证书输出目录')
args=parser.parse_args()
ip=ipaddress.ip_address(args.ip)
if not isinstance(ip,ipaddress.IPv4Address) or not ip.is_private or ip.is_loopback:
    parser.error('请选择手机能访问的私有局域网 IPv4，不能使用 127.0.0.1 或公网 IP')
out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
ca_key_file=out/'ca.key';ca_cert_file=out/'ca.crt'
if ca_key_file.exists() and ca_cert_file.exists():
    ca_key=serialization.load_pem_private_key(ca_key_file.read_bytes(),password=None)
    ca_cert=x509.load_pem_x509_certificate(ca_cert_file.read_bytes())
else:
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'RoadSense Local Testing CA')])
    now=datetime.now(timezone.utc)
    ca_cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
             .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=730))
             .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
             .add_extension(x509.KeyUsage(True,False,False,False,False,True,True,False,False),critical=True)
             .sign(ca_key,hashes.SHA256()))
    ca_key_file.write_bytes(ca_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    ca_key_file.chmod(0o600)
    ca_cert_file.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
now=datetime.now(timezone.utc)
subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,str(ip))])
cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(ca_cert.subject)
      .public_key(key.public_key()).serial_number(x509.random_serial_number())
      .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=365))
      .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
      .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip),x509.IPAddress(ipaddress.ip_address('127.0.0.1')),x509.DNSName('localhost')]),critical=False)
      .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
      .sign(ca_key,hashes.SHA256()))
(out/'lan.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
(out/'lan.key').chmod(0o600)
(out/'lan.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
print(f'创建成功：{out}')
print('在 qianduan/.env 中配置：')
print('PORT=5443')
print(f'LAN_HOST={ip}')
print(f'PHONE_PUBLIC_URL=https://{ip}:5443/phone')
print('SSL_CERT_FILE=certs/lan.crt')
print('SSL_KEY_FILE=certs/lan.key')
print('仅将 certs/ca.crt 安装并信任到手机系统的证书管理界面；绝不可传输 certs/ca.key。')
print('确保电脑与手机同一网络、防火墙放通 5443/TCP。')
