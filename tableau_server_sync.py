#!/usr/bin/env python3
"""
Sincronizador Tableau → GitHub (Online y Server)
Optimizado para Personal Access Token con caracteres especiales
"""

import os
import sys
import base64
import requests
import json
from datetime import datetime
from typing import List, Optional
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TableauAPI:
    """Cliente para API de Tableau"""
    
    def __init__(self, server: str, site: str, username: str, password: str):
        self.server = server
        self.site = site
        self.username = username
        self.password = password
        self.token = None
        self.base_url = f"{server}/api/3.17"
        
        if "online.tableau.com" in server:
            self.tableau_type = "ONLINE"
            logger.info("Detectado: Tableau Online")
        else:
            self.tableau_type = "SERVER"
            logger.info("Detectado: Tableau Server")
        
    def authenticate(self) -> bool:
        """Autenticar usando Personal Access Token"""
        try:
            auth_url = f"{self.base_url}/auth/signin"
            
            payload = {
                "credentials": {
                    "name": self.username,
                    "password": self.password,
                    "site": {
                        "contentUrl": self.site
                    }
                }
            }
            
            logger.info("Intentando autenticar...")
            logger.info(f"Servidor: {self.server}")
            logger.info(f"Sitio: {self.site}")
            logger.info(f"Usuario: {self.username}")
            
            json_payload = json.dumps(payload)
            
            response = requests.post(
                auth_url,
                data=json_payload,
                headers={
                    "Content-Type": "application/json; charset=UTF-8"
                },
                timeout=10
            )
            
            logger.info(f"Respuesta: {response.status_code}")
            
            if response.status_code == 401:
                logger.error("Error 401: Credenciales invalidas")
                logger.error("El token puede estar vencido o ser incorrecto")
                logger.error(f"Respuesta: {response.text[:200]}")
                return False
            
            if response.status_code == 403:
                logger.error("Error 403: Acceso prohibido")
                logger.error("Posible causa: Restricciones de la empresa")
                return False
            
            if response.status_code == 400:
                logger.error("Error 400: Bad Request")
                logger.error("Verifica el formato del token")
                logger.error(f"Respuesta: {response.text[:200]}")
                return False
            
            response.raise_for_status()
            
            data = response.json()
            self.token = data['credentials']['token']
            
            logger.info(f"Autenticacion exitosa en Tableau {self.tableau_type}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error de autenticacion: {e}")
            return False
        except Exception as e:
            logger.error(f"Error inesperado: {e}")
            return False
    
    def get_workbooks(self) -> List[dict]:
        """Obtener lista de workbooks"""
        try:
            headers = {"X-Tableau-Auth": self.token}
            workbooks_url = f"{self.base_url}/sites/{self.site}/workbooks"
            
            logger.info("Obteniendo lista de workbooks...")
            
            response = requests.get(workbooks_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            workbook_list = data.get('workbook', [])
            
            logger.info(f"Se encontraron {len(workbook_list)} workbooks")
            return workbook_list
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error al obtener workbooks: {e}")
            return []
    
    def download_workbook(self, workbook_id: str, workbook_name: str, 
                          download_path: str) -> Optional[str]:
        """Descargar un workbook"""
        try:
            headers = {"X-Tableau-Auth": self.token}
            download_url = (f"{self.base_url}/sites/{self.site}/"
                           f"workbooks/{workbook_id}/content")
            
            logger.info(f"Descargando: {workbook_name}...")
            
            response = requests.get(download_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            file_path = os.path.join(download_path, f"{workbook_name}.twbx")
            with open(file_path, 'wb') as f:
                f.write(response.content)
            
            logger.info(f"Descargado: {workbook_name}.twbx")
            return file_path
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error al descargar {workbook_name}: {e}")
            return None


class GitHubAPI:
    """Cliente para GitHub"""
    
    def __init__(self, repo_owner: str, repo_name: str, token: str):
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.token = token
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
    
    def upload_file(self, file_path: str, github_path: str, 
                    commit_message: str) -> bool:
        """Subir archivo a GitHub"""
        try:
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            encoded_content = base64.b64encode(file_content).decode('utf-8')
            
            url = (f"{self.base_url}/repos/{self.repo_owner}/{self.repo_name}/"
                   f"contents/{github_path}")
            
            sha = None
            try:
                response = requests.get(url, headers=self.headers)
                if response.status_code == 200:
                    sha = response.json()['sha']
            except:
                pass
            
            payload = {
                "message": commit_message,
                "content": encoded_content,
                "branch": "main"
            }
            
            if sha:
                payload["sha"] = sha
            
            response = requests.put(url, json=payload, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Subido a GitHub: {github_path}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error al subir a GitHub: {e}")
            return False


def main():
    """Funcion principal"""
    
    logger.info("=" * 70)
    logger.info("Backup Tableau → GitHub")
    logger.info("=" * 70)
    logger.info("")
    
    tableau_server = os.getenv('TABLEAU_SERVER', '')
    tableau_site = os.getenv('TABLEAU_SITE', '')
    tableau_user = os.getenv('TABLEAU_USERNAME', '')
    tableau_password = os.getenv('TABLEAU_PASSWORD', '')
    
    github_owner = os.getenv('GITHUB_REPO_OWNER', '')
    github_repo = os.getenv('GITHUB_REPO_NAME', '')
    github_token = os.getenv('GITHUB_TOKEN', '')
    
    required_vars = [
        ('TABLEAU_SERVER', tableau_server),
        ('TABLEAU_SITE', tableau_site),
        ('TABLEAU_USERNAME', tableau_user),
        ('TABLEAU_PASSWORD', tableau_password),
        ('GITHUB_REPO_OWNER', github_owner),
        ('GITHUB_REPO_NAME', github_repo),
        ('GITHUB_TOKEN', github_token),
    ]
    
    for var_name, var_value in required_vars:
        if not var_value:
            logger.error(f"Variable de entorno requerida: {var_name}")
            sys.exit(1)
    
    download_dir = "tableau_workbooks"
    os.makedirs(download_dir, exist_ok=True)
    
    tableau = TableauAPI(tableau_server, tableau_site, tableau_user, tableau_password)
    if not tableau.authenticate():
        logger.error("No se pudo autenticar en Tableau")
        sys.exit(1)
    
    workbooks = tableau.get_workbooks()
    if not workbooks:
        logger.warning("No se encontraron workbooks")
        sys.exit(0)
    
    github = GitHubAPI(github_owner, github_repo, github_token)
    
    logger.info("")
    logger.info("Descargando y subiendo workbooks...")
    logger.info("")
    
    uploaded_count = 0
    for workbook in workbooks:
        wb_id = workbook.get('id')
        wb_name = workbook.get('name')
        
        if not wb_id or not wb_name:
            continue
        
        file_path = tableau.download_workbook(wb_id, wb_name, download_dir)
        if not file_path:
            continue
        
        github_path = f"workbooks/{wb_name}.twbx"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_message = f"Backup: {wb_name} - {timestamp}"
        
        if github.upload_file(file_path, github_path, commit_message):
            uploaded_count += 1
    
    logger.info("")
    logger.info("=" * 70)
    logger.info(f"Completado: {uploaded_count}/{len(workbooks)} workbooks")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
