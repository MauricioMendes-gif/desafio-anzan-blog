#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🚀 Blogger to GitHub Pages Migrator (Versão Final Corrigida)
"""

import os
import re
import sys
import json
import logging
import requests
import html2text
import frontmatter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Configurações de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('migration.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/blogger']

class BloggerMigrator:
    def __init__(self, client_secret_file='client_secret.json', 
                 output_dir='_posts', assets_dir='assets/images'):
        self.client_secret_file = client_secret_file
        self.output_dir = Path(output_dir)
        self.assets_dir = Path(assets_dir)
        self.service = None
        self.blog_id = None
        self.image_map = {}
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        
        self.html_converter = html2text.HTML2Text()
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = False
        self.html_converter.body_width = 0
        
    def authenticate(self):
        logger.info("🔐 Iniciando autenticação...")
        creds = None
        token_file = 'token.json'
        
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secret_file, SCOPES)
                creds = flow.run_local_server(port=0)
            
            with open(token_file, 'w', encoding='utf-8') as token:
                token.write(creds.to_json())
        
        self.service = build('blogger', 'v3', credentials=creds)
        logger.info("✓ Serviço Blogger conectado!")
        return True
    
    def get_blog_id(self, blog_url=None):
        if blog_url:
            try:
                blog = self.service.blogs().getByUrl(url=blog_url).execute()
                self.blog_id = blog['id']
                logger.info(f"✓ Blog encontrado: {blog['name']}")
                return self.blog_id
            except Exception as e:
                logger.error(f"❌ Erro ao buscar blog por URL: {e}")
        
        blogs = self.service.users().getBlogs(userId='self').execute()
        if not blogs.get('items'):
            return None
        
        if len(blogs['items']) == 1:
            selected = blogs['items'][0]
        else:
            print("\n📝 Seus blogs:")
            for i, blog in enumerate(blogs['items'], 1):
                print(f"  {i}. {blog['name']}")
            choice = input("Escolha o número (padrão: 1): ") or "1"
            selected = blogs['items'][int(choice) - 1]
            
        self.blog_id = selected['id']
        return self.blog_id
    
    def download_image(self, url, post_slug):
        if url in self.image_map:
            return self.image_map[url]
        
        try:
            parsed = urlparse(url)
            filename = Path(parsed.path).name
            if not filename or '.' not in filename:
                filename = f"image_{len(self.image_map)}.jpg"
            
            filename = re.sub(r'[^\w\.\-]', '_', filename)
            filename = f"{post_slug[:30]}_{filename}"
            
            local_path = self.assets_dir / filename
            relative_path = f"/{self.assets_dir}/{filename}"
            
            if not local_path.exists():
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                with open(local_path, 'wb') as f:
                    f.write(response.content)
            
            self.image_map[url] = relative_path
            return relative_path
        except Exception as e:
            logger.warning(f"⚠️ Falha imagem: {url[:50]}...")
            return url

    def process_content(self, html_content, post_slug):
        if not html_content:
            return ""
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src')
            if src and src.startswith('http'):
                local_path = self.download_image(src, post_slug)
                img['src'] = local_path
                for attr in ['width', 'height', 'style', 'class', 'border']:
                    img.pop(attr, None)
        
        try:
            markdown = self.html_converter.handle(str(soup))
        except Exception as e:
            logger.error(f"Erro na conversão HTML->MD: {e}")
            markdown = f"\n<!-- Erro na conversão. HTML Original abaixo -->\n{html_content}\n"
        
        return markdown.strip()
    
    def sanitize_filename(self, title):
        slug = title.lower()
        slug = slug.encode('ascii', 'ignore').decode('ascii')
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        return slug[:60]
    
    def create_post_file(self, post, index, total):
        try:
            title = post.get('title', 'Sem título')
            content_html = post.get('content', '')
            published = post.get('published', '')
            labels = post.get('labels', [])
            post_id = post.get('id', '')
            
            try:
                post_date = datetime.fromisoformat(published.replace('Z', '+00:00'))
            except:
                post_date = datetime.now()
            
            slug = self.sanitize_filename(title)
            date_str = post_date.strftime('%Y-%m-%d')
            filename = f"{date_str}-{slug}.md"
            filepath = self.output_dir / filename
            
            logger.info(f"[{index}/{total}] Processando: {title[:40]}...")
            
            markdown_content = self.process_content(content_html, slug)
            
            if not markdown_content and not content_html:
                logger.warning(f"⚠️ Post vazio: {title}")
                return False

            frontmatter_data = {
                'layout': 'post',
                'title': title,
                'date': post_date.strftime('%Y-%m-%d %H:%M:%S %z'),
                'categories': labels[:1] if labels else ['blog'],
                'tags': labels,
                'blogger_id': post_id,
                'published': True
            }
            
            post_obj = frontmatter.Post(markdown_content)
            post_obj.metadata.update(frontmatter_data)
            
            # CORREÇÃO: Usar dumps() para gerar string
            content_to_write = frontmatter.dumps(post_obj)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content_to_write)
            
            if filepath.stat().st_size == 0:
                logger.error(f"❌ Arquivo criado mas vazio: {filename}")
                return False
                
            logger.info(f"✓ Salvo: {filename} ({filepath.stat().st_size} bytes)")
            return True
            
        except Exception as e:
            logger.error(f"❌ ERRO CRÍTICO ao processar post '{post.get('title')}': {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def migrate_posts(self, max_posts=None, status='LIVE'):
        if not self.service or not self.blog_id:
            logger.error("❌ Serviço não inicializado!")
            return False
        
        logger.info(f"🚀 Iniciando migração...")
        all_posts = []
        page_token = None
        
        while True:
            try:
                request = self.service.posts().list(
                    blogId=self.blog_id,
                    status=status,
                    fetchBodies=True,
                    fetchImages=True,
                    maxResults=50,
                    pageToken=page_token
                )
                response = request.execute()
                posts = response.get('items', [])
                all_posts.extend(posts)
                
                page_token = response.get('nextPageToken')
                if not page_token or (max_posts and len(all_posts) >= max_posts):
                    break
            except Exception as e:
                logger.error(f"❌ Erro API: {e}")
                break
        
        if not all_posts:
            logger.warning("⚠️ Nenhum post encontrado!")
            return False
        
        if max_posts:
            all_posts = all_posts[:max_posts]
            
        success_count = 0
        for i, post in enumerate(all_posts, 1):
            if self.create_post_file(post, i, len(all_posts)):
                success_count += 1
        
        logger.info(f"\n{'='*50}")
        logger.info(f"✅ Migração concluída!")
        logger.info(f"📊 Sucesso: {success_count}/{len(all_posts)} posts")
        logger.info(f"{'='*50}\n")
        return success_count > 0

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Migrador Blogger -> Jekyll')
    parser.add_argument('--blog-url', '-u', help='URL do Blog')
    parser.add_argument('--max-posts', '-n', type=int, help='Max posts')
    args = parser.parse_args()
    
    migrator = BloggerMigrator()
    if not migrator.authenticate():
        sys.exit(1)
    if not migrator.get_blog_id(args.blog_url):
        sys.exit(1)
    migrator.migrate_posts(max_posts=args.max_posts)

if __name__ == '__main__':
    main()
