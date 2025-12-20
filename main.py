import os
import time
import json
import re
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright
import pdfplumber

# --- CONFIGURAÇÕES ---
URL_POWERBI = "https://app.powerbi.com/view?r=eyJrIjoiNmJlOTI0ZTYtY2UwNi00NmZmLWE1NzQtNjUwNjUxZTk3Nzg0IiwidCI6ImFkMjI2N2U3LWI4ZTctNDM4Ni05NmFmLTcxZGVhZGQwODY3YiJ9"
ARQUIVO_PDF = "temp_powerbi_snapshot.pdf"
ARQUIVO_JSON = "relacao_atualizada.json"

# --- CONFIGURAÇÕES SUPABASE ---
SUPABASE_PROJECT_URL = "https://nhnejoanmggvinnfphir.supabase.co"
SUPABASE_BUCKET = "consorciorecon-json"
SUPABASE_FILE_NAME = "relacao_atualizada.json"

# Painel do Supabase -> Settings -> API -> service_role (secret)
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5obmVqb2FubWdndmlubmZwaGlyIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NjA3NDk5NCwiZXhwIjoyMDgxNjUwOTk0fQ._QXfa-v4YBC_-xazB4A6LrWeB-oxXiIFfboiqbNQh7Q" 

# --- FUNÇÕES DE LIMPEZA E REPARO ---

def limpar_inteiro(valor):
    if isinstance(valor, int): return valor
    if not valor: return 0
    limpo = re.sub(r'\D', '', str(valor))
    return int(limpo) if limpo else 0

def limpar_texto(valor):
    if valor is None: return ""
    return str(valor).replace("\n", "").replace("\r", "").strip()

def limpar_credito(valor):
    val = limpar_texto(valor)
    if " A R$" not in val and "AR$" in val:
        val = val.replace("AR$", " A R$")
    return val

def reparar_linha_colunas(cols):
    if len(cols) == 13: return cols
    if len(cols) < 13: return cols 

    parte_inicial = cols[:7]
    resto = cols[7:]
    
    idx_inicio_lances = -1
    for i, val in enumerate(resto):
        val_upper = str(val).upper().strip()
        if val_upper in ["SIM", "NÃO"]:
            idx_inicio_lances = i
            break
    
    if idx_inicio_lances == -1: return cols

    fragmentos_credito = resto[:idx_inicio_lances]
    credito_reconstruido = " ".join([str(x) for x in fragmentos_credito if x])
    parte_lances = resto[idx_inicio_lances:]
    
    return parte_inicial + [credito_reconstruido] + parte_lances

# --- FUNÇÕES DE DATA/HORA ---

def formatar_data_hora(texto_bruto):
    padrao = r"(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM))"
    match = re.search(padrao, texto_bruto, re.IGNORECASE)
    
    if match:
        data_str = match.group(1)
        try:
            dt_obj = datetime.strptime(data_str, "%m/%d/%Y %I:%M:%S %p")
            return dt_obj.strftime("%d/%m/%Y %H:%M:%S")
        except ValueError:
            return data_str
    return texto_bruto.replace("Relação de Grupos e suas Características", "").strip()

def extrair_timestamp_relatorio(page):
    linhas = page.extract_text_lines()
    bbox_atualizacao = None
    for line in linhas:
        texto_limpo = line['text'].lower().replace("ã", "a").replace("ç", "c")
        if "atualizacao" in texto_limpo:
            bbox_atualizacao = line
            break
    
    if not bbox_atualizacao: return "Data não encontrada"

    linhas_acima = [l for l in linhas if l['bottom'] < bbox_atualizacao['top']]
    if not linhas_acima: return "Data não encontrada"

    linhas_acima.sort(key=lambda x: x['top'])
    texto_bruto = linhas_acima[-1]['text'].strip()
    return formatar_data_hora(texto_bruto)

# --- EXTRAÇÃO PRINCIPAL ---

def extrair_dados_pdf(caminho_pdf):
    print(f"[*] Processando PDF: {caminho_pdf}")
    lista_grupos = []
    data_atualizacao_global = "Não identificada"
    
    table_settings = {"vertical_strategy": "text", "horizontal_strategy": "text", "snap_tolerance": 4}

    with pdfplumber.open(caminho_pdf) as pdf:
        for p_idx, page in enumerate(pdf.pages):
            if p_idx == 0:
                data_atualizacao_global = extrair_timestamp_relatorio(page)
                print(f"      [INFO] Data Formatada: {data_atualizacao_global}")

            # Mapeamento de Headers
            headers_map = []
            padrao_regex = re.compile(r"Vencimento dia\s*(\d+).*?Próxima Assembleia\s*-\s*(\d{2}/\d{2}/\d{4})")
            matches = page.search(padrao_regex)
            for match in matches:
                texto_encontrado = match["text"]
                dados_match = padrao_regex.search(texto_encontrado)
                if dados_match:
                    headers_map.append({
                        "top": match["top"],
                        "dia": int(dados_match.group(1)),
                        "data": dados_match.group(2)
                    })
            headers_map.sort(key=lambda x: x['top'])

            # Tabelas
            tabelas = page.find_tables(table_settings)
            
            for tabela in tabelas:
                dados_tabela = tabela.extract(x_tolerance=5)
                if not dados_tabela: continue

                idx_inicio = 0
                for i, row in enumerate(dados_tabela):
                    s = "".join([str(c) for c in row if c]).lower()
                    if "grupo" in s and "especie" in s:
                        idx_inicio = i + 1
                        break
                
                linhas_objetos = tabela.rows 
                for i, row_data in enumerate(dados_tabela[idx_inicio:]):
                    idx_real = idx_inicio + i
                    try:
                        linha_obj = linhas_objetos[idx_real]
                        linha_y = linha_obj.cells[0][1] 
                    except:
                        linha_y = tabela.bbox[1] 

                    header_ativo = {"dia": 10, "data": "A definir"} 
                    headers_acima = [h for h in headers_map if h['top'] < linha_y]
                    if headers_acima:
                        header_ativo = headers_acima[-1]
                    
                    cols = [limpar_texto(c) for c in row_data if c is not None and str(c).strip() != ""]
                    if not cols or not cols[0].isdigit(): continue
                    cols = reparar_linha_colunas(cols)
                    if len(cols) < 13: continue

                    try:
                        item = {
                            "Grupo": int(cols[0]),
                            "Espécie": cols[1],
                            "Vagas": cols[2], 
                            "Duração Padrão": limpar_inteiro(cols[3]),
                            "Ass. Realizadas": limpar_inteiro(cols[4]),
                            "Prazo Máx. Vendas": limpar_inteiro(cols[5]),
                            "Máx. Cotas": limpar_inteiro(cols[6]),
                            "Créditos Disponíveis": limpar_credito(cols[7]),
                            "Lance Normal": cols[8],
                            "Lance Fixo": cols[9],
                            "Carta Avaliação": cols[10],
                            "Lance FGTS": cols[11],
                            "Lance Embutido (25%)": cols[12],
                            "Dia do Vencimento": header_ativo['dia'],
                            "Próxima Assembleia": header_ativo['data']
                        }
                        lista_grupos.append(item)
                    except Exception as e:
                        pass

    return {
        "ultima_atualizacao": data_atualizacao_global,
        "grupos": lista_grupos
    }

# --- FUNÇÃO DE UPLOAD SUPABASE ---

def upload_para_supabase(caminho_local_json):
    """Lê o arquivo local e faz upload (overwrite) no Supabase"""
    print(f"[*] Iniciando Upload para Supabase...")
    
    if "SUA_CHAVE" in SUPABASE_KEY:
        print("[ERRO] Você precisa configurar a SUPABASE_KEY no topo do script.")
        return

    # Endpoint da API Storage do Supabase
    url = f"{SUPABASE_PROJECT_URL}/storage/v1/object/{SUPABASE_BUCKET}/{SUPABASE_FILE_NAME}"
    
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "x-upsert": "true",  # Força a substituição do arquivo existente
        "Content-Type": "application/json"
    }
    
    try:
        with open(caminho_local_json, 'rb') as f:
            dados = f.read()
            
        response = requests.post(url, headers=headers, data=dados)
        
        if response.status_code in [200, 201]:
            print(f"[SUCESSO] Upload concluído!")
            print(f"Link público: {SUPABASE_PROJECT_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{SUPABASE_FILE_NAME}")
        else:
            print(f"[ERRO NO UPLOAD] Status: {response.status_code}")
            print(f"Detalhes: {response.text}")
            
    except Exception as e:
        print(f"[ERRO DE CONEXÃO] {e}")

def main():
    print("--- RELACAO DE GRUPOS SCRAPER + UPLOAD NA NUVEM ---")
    
    # 1. Playwright
    with sync_playwright() as p:
        print("[*] Abrindo navegador...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        try:
            print(f"[*] Acessando: {URL_POWERBI}")
            page.goto(URL_POWERBI, wait_until="networkidle", timeout=60000)
            page.wait_for_selector(".visualContainer", state="visible", timeout=40000)
            print("[*] Renderizando (10s)...")
            time.sleep(10)
            print("[*] Gerando PDF...")
            page.pdf(path=ARQUIVO_PDF, format="A4", landscape=True, print_background=True)
        except Exception as e:
            print(f"[ERRO] {e}")
            browser.close()
            return
        browser.close()

    # 2. Extração
    if os.path.exists(ARQUIVO_PDF):
        resultado_final = extrair_dados_pdf(ARQUIVO_PDF)
        
        if resultado_final["grupos"]:
            resultado_final["grupos"].sort(key=lambda x: x["Grupo"])
            
            with open(ARQUIVO_JSON, "w", encoding="utf-8") as f:
                json.dump(resultado_final, f, indent=2, ensure_ascii=False)
                
            print(f"\n[SUCESSO] JSON local gerado: {os.path.abspath(ARQUIVO_JSON)}")
            
            # 3. Upload para Nuvem
            upload_para_supabase(ARQUIVO_JSON)
            
        else:
            print("[ERRO] Nenhum grupo extraído.")
    else:
        print("[ERRO] PDF não encontrado.")

if __name__ == "__main__":
    main()