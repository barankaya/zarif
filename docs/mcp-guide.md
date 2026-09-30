# Zarif MCP Kullanım Kılavuzu

Bu doküman, zarif'i bir **MCP (Model Context Protocol) sunucusu** olarak
çalıştırma, herhangi bir MCP host'una (VS Code, Claude Desktop, kendi
istemciniz) bağlama ve sunduğu araçları kullanma sürecini uçtan uca anlatır.

İçindekiler:

1. [MCP nedir, zarif'te neden var](#1-mcp-nedir-zarifte-neden-var)
2. [Kurulum](#2-kurulum)
3. [Sunucuyu başlatma](#3-sunucuyu-başlatma)
4. [Host konfigürasyonu](#4-host-konfigürasyonu)
5. [Araç referansı](#5-araç-referansı)
6. [Uçtan uca örnek akış](#6-uçtan-uca-örnek-akış)
7. [Sampling yerine sabit bir model kullanma](#7-sampling-yerine-sabit-bir-model-kullanma)
8. [Güvenlik ve davranış notları](#8-güvenlik-ve-davranış-notları)
9. [Sorun giderme](#9-sorun-giderme)
10. [Bilinen sınırlamalar](#10-bilinen-sınırlamalar)

## 1. MCP nedir, zarif'te neden var

[Model Context Protocol](https://modelcontextprotocol.io/), bir "sunucu"nun
(burada zarif) bir "host/istemci"ye (VS Code Copilot Chat, Claude Desktop,
başka bir MCP-uyumlu ajan) **araç (tool)** sunmasını sağlayan standart bir
protokoldür. Host, sunucunun tanıttığı araçları kendi konuşma akışı içinde
çağırabilir.

MCP ayrıca **sampling** adında bir yetenek tanımlar: sunucu, kendi başına bir
LLM'e bağlı olmak zorunda kalmadan, "şu mesajları tamamla" isteğini host'a
geri gönderebilir — host bu isteği kendi bağlı olduğu modelle (ör. Copilot
Chat'in kullandığı model) yanıtlar ve sonucu sunucuya döner.

Zarif bunu şu şekilde kullanır: `zarif_propose_edit` aracı çalışırken editör/
doğrulayıcı LLM adımları için **kendi model sağlayıcısını kurmaz**; bunun
yerine sampling ile host'un modelini kullanır. Sonuç: zarif'i hangi MCP
host'ta çalıştırırsanız çalıştırın, o host'un modeliyle otomatik çalışır —
ayrı bir Ollama/OpenAI kurulumu veya API anahtarı gerekmez.

## 2. Kurulum

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[mcp]"
```

Bu adım internet erişimi gerektirir (PyPI'den `mcp` paketini indirir).

## 3. Sunucuyu başlatma

```powershell
zarif serve
```

Bu komut zarif'i **stdio** transport'u üzerinden bir MCP sunucusu olarak
başlatır ve terminali bloke eder (host tarafından bir alt süreç olarak
başlatılması beklenir — genelde bunu elle çalıştırmazsınız, aşağıdaki host
konfigürasyonlarında host bunu sizin için yapar).

Elle/tekil test için doğrudan çalıştırıp `Ctrl+C` ile durdurabilirsiniz;
ama gerçek kullanım için bir sonraki bölümdeki host konfigürasyonunu tercih
edin.

## 4. Host konfigürasyonu

MCP host'ları genelde bir JSON dosyasında "bu komutu çalıştırarak bir MCP
sunucusuna bağlan" tanımı bekler. Zarif için komut, projenizin sanal
ortamındaki `zarif` çalıştırılabilir dosyasıdır.

### VS Code (Copilot Chat / Agent mode)

Çalışma alanı kökünde `.vscode/mcp.json`:

```jsonc
{
  "servers": {
    "zarif": {
      "type": "stdio",
      "command": "C:\\path\\to\\zarif\\.venv\\Scripts\\zarif.exe",
      "args": ["serve"]
    }
  }
}
```

VS Code'da Command Palette → "MCP: List Servers" ile durumunu kontrol
edebilir, "MCP: Restart Server" ile yeniden başlatabilirsiniz. Sampling
isteklerini onaylamanız istenebilir (VS Code, bir MCP sunucusunun modelinizi
kullanmasına izin vermeden önce onay ister) — bu normaldir, kabul edin.

### Claude Desktop

`claude_desktop_config.json` (Ayarlar → Developer → Edit Config ile
açılır):

```json
{
  "mcpServers": {
    "zarif": {
      "command": "C:\\path\\to\\zarif\\.venv\\Scripts\\zarif.exe",
      "args": ["serve"]
    }
  }
}
```

Kaydettikten sonra Claude Desktop'ı yeniden başlatın; araç çubuğunda
"zarif" sunucusunu ve iki aracını görmelisiniz.

### Diğer MCP host'ları

Genel desen aynıdır: host'a "`command` + `args` ile stdio üzerinden bir alt
süreç başlat" talimatını verirsiniz. `command` alanına sanal ortamınızdaki
`zarif` (Windows'ta `zarif.exe`) çalıştırılabilir dosyasının tam yolunu,
`args` alanına `["serve"]` yazmanız yeterli. Host'unuz sampling'i
desteklemiyorsa bkz. [Bölüm 7](#7-sampling-yerine-sabit-bir-model-kullanma).

## 5. Araç referansı

### `zarif_propose_edit`

Bir markdown dosyasını sadeleştirir, **hiçbir dosyaya yazmadan** diff önerir.

| Parametre | Tip | Varsayılan | Açıklama |
|---|---|---|---|
| `file_path` | `string` | (zorunlu) | Düzenlenecek markdown dosyasının yolu. |
| `max_attempts` | `integer` | `2` | `verify_retention` başarısız olursa kaç kez yeniden denenir. |
| `editor_model` | `string \| null` | `null` | Verilirse sampling yerine bu sağlayıcı kullanılır (bkz. Bölüm 7). |

Dönüş:

```jsonc
{
  "session_id": "3f1c...-uuid",
  "diff": "--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@ ...",
  "sufficient": true,
  "warnings": [],
  "token_report": { "before": 1200, "after": 410, "reduction_pct": 65.8 }
}
```

- `sufficient: false` ise doğrulayıcı bazı kritik maddelerin kaybolduğunu
  düşünüyor demektir; `warnings` içinde ayrıntı bulunur. Yine de `diff` ve
  `session_id` döner — dosyayı yazıp yazmamaya siz karar verirsiniz.
- `session_id`, LangGraph'ın altında yatan `thread_id`'sidir; bir sonraki
  adımda (`zarif_apply_edit`) bu id ile devam ettirilir. Sunucu süreci
  hayatta olduğu sürece bellekte tutulur — sunucu yeniden başlatılırsa
  bekleyen session'lar kaybolur, `zarif_propose_edit`'i tekrar çağırmanız
  gerekir.

### `zarif_apply_edit`

`zarif_propose_edit`'in önerdiği değişikliği onaylar ve dosyaya yazar.

| Parametre | Tip | Açıklama |
|---|---|---|
| `session_id` | `string` | `zarif_propose_edit`'in döndürdüğü id. |

Dönüş:

```json
{ "written": true, "backup_path": "CLAUDE.md.bak" }
```

Bilinmeyen/kullanılmış bir `session_id` verilirse hata döner
(`Bilinmeyen session_id: ... (önce zarif_propose_edit çağırın)`).

## 6. Uçtan uca örnek akış

Host'unuzdaki sohbette (ör. "CLAUDE.md dosyasını sadeleştir" dediğinizde
ajan şu adımları izler):

1. Ajan `zarif_propose_edit(file_path="CLAUDE.md")` çağırır.
2. Zarif, editör/doğrulayıcı adımları için sizin sohbet ettiğiniz modele
   sampling istekleri gönderir (host bunu sizin adınıza, genelde sessizce
   veya bir onay istemiyle yürütür).
3. Ajan size diff'i gösterir, "bu değişiklikleri uygulayayım mı?" diye sorar.
4. Onaylarsanız ajan `zarif_apply_edit(session_id="...")` çağırır, dosya
   yedeklenip yazılır.

## 7. Sampling yerine sabit bir model kullanma

Bazı MCP host'ları (veya otomasyon/CI senaryoları) sampling'i desteklemez ya
da siz belirli bir modeli sabitlemek istersiniz. Bu durumda
`zarif_propose_edit`'e `editor_model` parametresini açıkça verin:

```jsonc
{ "file_path": "CLAUDE.md", "editor_model": "ollama:llama3.1" }
```

Bu durumda sampling hiç kullanılmaz; `ollama:`/`openai:` sağlayıcıları
(bkz. [`src/zarif/models.py`](../src/zarif/models.py)) devreye girer ve
o sağlayıcının kendi kurulumu (yerel Ollama sunucusu / `OPENAI_API_KEY`)
gerekir.

## 8. Güvenlik ve davranış notları

- `zarif_propose_edit` **hiçbir zaman** dosyaya yazmaz; yazma işlemi yalnızca
  ayrı ve açık bir `zarif_apply_edit` çağrısıyla gerçekleşir. Bu, CLI'daki
  `interrupt_before=["write"]` onay noktasının MCP karşılığıdır.
- `zarif_apply_edit` öncesinde her zaman bir `.bak` yedeği alınır
  (bkz. `io_utils.make_backup`).
- Sampling isteklerinin içeriği (dosyanızın metni) host'a ve host'un bağlı
  olduğu modele gönderilir — hassas içerikli dosyalarda host'unuzun veri
  işleme politikasını göz önünde bulundurun.
- Sunucu tek bir sürecin belleğinde `session_id → graph state` eşlemesini
  tutar; birden fazla `zarif serve` örneği paralel çalıştırırsanız
  session'lar birbirinden izole kalır (paylaşılmaz).

## 9. Sorun giderme

| Belirti | Olası neden / çözüm |
|---|---|
| `RuntimeError: MCP sunucusu için 'mcp' paketi gerekli` | `pip install -e ".[mcp]"` çalıştırılmamış. |
| Host "zarif" sunucusunu listelemiyor | `command` yolu yanlış olabilir; `.venv\Scripts\zarif.exe`'nin gerçekten var olduğunu doğrulayın (`pip install -e .` sonrası oluşur). |
| `zarif_apply_edit` "Bilinmeyen session_id" hatası veriyor | Sunucu bu çağrı öncesinde yeniden başlatılmış (session bellekte kayboldu) ya da aynı `session_id` daha önce zaten uygulanmış. `zarif_propose_edit`'i tekrar çağırın. |
| Sampling çağrısı hiç yanıt vermiyor / host onay bekliyor | Bazı host'lar sampling isteklerini kullanıcı onayına bağlar; host'unuzun bildirim/istem alanını kontrol edin. |
| `editor_model` ile hata (`langchain-ollama`/`langchain-openai` eksik) | İlgili opsiyonel bağımlılık grubunu kurun: `pip install -e ".[local]"` / `".[api]"`. |

## 10. Bilinen sınırlamalar

- Bu entegrasyon ağ erişimi olmayan bir geliştirme ortamında yazıldı; `mcp`
  paketinin `SamplingMessage` / `ctx.session.create_message(...)` imzası
  resmi dokümana göre uygulandı ama kurulu bir paket karşısında **test
  edilmedi**. Gerçek bir host ile ilk denemede küçük imza farkları çıkarsa
  [`src/zarif/mcp_server.py`](../src/zarif/mcp_server.py) içindeki
  `SamplingChatModel._asample` metodunu güncel `mcp` sürümüne göre
  düzeltmeniz gerekebilir.
- Sunucu tek bir dosya üzerinde aynı anda tek bir bekleyen session'ı
  destekler mantığıyla tasarlanmadı — aynı dosya için art arda birden fazla
  `zarif_propose_edit` çağrısı birden fazla bağımsız session üretir, hepsi
  ayrı ayrı `zarif_apply_edit` ile uygulanabilir (son yazan kazanır).
