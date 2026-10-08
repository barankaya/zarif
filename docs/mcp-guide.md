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
"zarif" sunucusunu ve araçlarını görmelisiniz.

### IntelliJ (AI Assistant / MCP Client eklentisi)

Konfigürasyon aynı `command`/`args` deseniyle çalışır (IDE ayarları → MCP
sunucuları → `mcp.json` benzeri bir dosya).

Gerçek testlerde IntelliJ'de iki farklı hata gözlemlendi: `Another MCP
sampling request is already in progress` (-32603) ve `Request timed out`
(-32001) — ikisi de aynı kök nedenden kaynaklanıyor: eski tasarımda tek bir
`zarif_propose_edit` çağrısı tek seferde 3-5 ardışık sampling round-trip'i
yapıyordu (extract_critical → propose_edit → verify_retention,
`max_attempts` ile tekrar), host'un kendi tool-call zaman aşımı bunu
karşılamayabiliyordu. **Önce MCP progress notification denendi, işe
yaramadı** (IntelliJ ilk denemeyi bile zaman aşımına uğratıyordu) — bu
yüzden zarif artık bu işi tamamen farklı çözüyor: `zarif_propose_edit`
hiçbir sampling çağrısı yapmadan anında döner, ve yeni
`zarif_continue_edit(session_id)` aracı her çağrıda **en fazla bir**
sampling round-trip'i yapar (bkz. Bölüm 5). Ajan `status: "ready_to_apply"`
olana kadar bu aracı tekrar tekrar çağırır — her tek tool çağrısı kısa
sürdüğü için IntelliJ'in tool-call zaman aşımını aşma riski neredeyse
ortadan kalkar — bunu yaparken host'un kendi modeli (sampling) kullanılmaya
devam eder, `editor_model`'e geçmek gerekmez.

### Diğer MCP host'ları

Genel desen aynıdır: host'a "`command` + `args` ile stdio üzerinden bir alt
süreç başlat" talimatını verirsiniz. `command` alanına sanal ortamınızdaki
`zarif` (Windows'ta `zarif.exe`) çalıştırılabilir dosyasının tam yolunu,
`args` alanına `["serve"]` yazmanız yeterli. Host'unuz sampling'i
desteklemiyorsa bkz. [Bölüm 7](#7-sampling-yerine-sabit-bir-model-kullanma).

## 5. Araç referansı

### `zarif_propose_edit`

Bir markdown dosyası için düzenleme oturumu **başlatır** — hiçbir sampling/LLM
çağrısı yapmadan **anında** döner (bkz. aşağıdaki "neden adım adım?" notu).

| Parametre | Tip | Varsayılan | Açıklama |
|---|---|---|---|
| `file_path` | `string` | (zorunlu) | Düzenlenecek markdown dosyasının yolu. |
| `max_attempts` | `integer` | `2` | `verify_retention` başarısız olursa kaç kez yeniden denenir. |
| `editor_model` | `string \| null` | `null` | Verilirse sampling yerine bu sağlayıcı kullanılır (bkz. Bölüm 7). |

Dönüş:

```jsonc
{ "session_id": "3f1c...-uuid", "status": "in_progress", "next_step": "extract_critical" }
```

`session_id`'yi hemen `zarif_continue_edit`'e verin.

### `zarif_continue_edit`

Bir oturumu **bir sonraki adıma** kadar ilerletir — en fazla **tek bir**
sampling round-trip'i yapar (`extract_critical`, `propose_edit` veya
`verify_retention` adımlarından biri). `status` hâlâ `"in_progress"` olduğu
sürece bu aracı tekrar tekrar çağırın.

| Parametre | Tip | Açıklama |
|---|---|---|
| `session_id` | `string` | `zarif_propose_edit`'in döndürdüğü id. |

Dönüş (devam ederken):

```jsonc
{ "session_id": "3f1c...-uuid", "status": "in_progress", "next_step": "propose_edit" }
```

Dönüş (hazır olduğunda, `status: "ready_to_apply"`):

```jsonc
{
  "session_id": "3f1c...-uuid",
  "status": "ready_to_apply",
  "diff": "--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@ ...",
  "sufficient": true,
  "warnings": [],
  "token_report": { "before": 1200, "after": 410, "reduction_pct": 65.8 }
}
```

- `sufficient: false` ise doğrulayıcı bazı kritik maddelerin kaybolduğunu
  düşünüyor demektir; `warnings` içinde ayrıntı bulunur. Yine de `diff` ve
  `session_id` döner — dosyayı yazıp yazmamaya siz karar verirsiniz.
- `session_id`, LangGraph'ın altında yatan `thread_id`'sidir; sunucu süreci
  hayatta olduğu sürece bellekte tutulur — sunucu yeniden başlatılırsa
  bekleyen session'lar kaybolur, `zarif_propose_edit`'i tekrar çağırmanız
  gerekir.

**Neden adım adım?** Bazı host'lar (ör. IntelliJ) tek bir tool çağrısı
içindeki birden fazla ardışık sampling round-trip'ini kendi tool-call zaman
aşımı içinde bekleyemiyor (`-32001 Request timed out`, bkz. Bölüm 9). Her
`zarif_continue_edit` çağrısı en fazla bir round-trip yaptığı için, host'un
kendi modeli (sampling) kullanılmaya devam ederken her tek tool çağrısı kısa
sürer.

### `zarif_apply_edit`

`zarif_continue_edit`'in `"ready_to_apply"` döndürdüğü değişikliği onaylar ve
dosyaya yazar.

| Parametre | Tip | Açıklama |
|---|---|---|
| `session_id` | `string` | `zarif_propose_edit`'in döndürdüğü id. |

Dönüş:

```json
{ "written": true, "backup_path": "CLAUDE.md.bak" }
```

Bilinmeyen bir `session_id` verilirse hata döner
(`Bilinmeyen session_id: ... (önce zarif_propose_edit çağırın)`); oturum henüz
`"ready_to_apply"` durumuna gelmediyse de hata döner (`zarif_continue_edit`'i
önce tamamlayın).

### `zarif_discard_edit`

`zarif_propose_edit` ile açılan, henüz uygulanmamış bir session'ı dosyaya
hiçbir şey yazmadan bellekten temizler. Kullanıcı öneriyi reddettiğinde host
bunu çağırmalı — aksi halde session, sunucu süreci hayatta olduğu sürece
bellekte birikmeye devam eder (bkz. Bölüm 10).

| Parametre | Tip | Açıklama |
|---|---|---|
| `session_id` | `string` | `zarif_propose_edit`'in döndürdüğü id. |

Dönüş:

```json
{ "discarded": true }
```

Bilinmeyen bir `session_id` verilirse hata vermez, `{"discarded": false}` döner
(idempotent — host'un temizlik çağrısını iki kez yapması güvenlidir).

### `zarif_restore_edit`

`zarif_apply_edit` ile yazılmış bir dosyayı en son alınan `.bak` yedeğinden
geri yükler — CLI'daki `zarif restore` komutunun MCP karşılığı. Yanlış
onaylanmış bir değişikliği geri almak için kullanılır.

| Parametre | Tip | Açıklama |
|---|---|---|
| `file_path` | `string` | Geri yüklenecek dosyanın yolu (session_id değil). |

Dönüş:

```json
{ "restored": true, "backup_path": "CLAUDE.md.bak" }
```

Yedek bulunamazsa hata döner (`Yedek bulunamadı: <dosya>.bak`).

### `zarif_benchmark_start` / `zarif_benchmark_continue` / `zarif_benchmark_discard`

CLI'daki `zarif benchmark` komutunun (gömülü 5 örnek dosya üzerinde token
azalması/bilgi korunumu ölçümü, bkz. README "Downstream benchmark") MCP
karşılığı — **hiçbir dosyaya yazmaz**, host'un kendi modelini kullanır,
ayrı bir Ollama/OpenAI kurulumu gerekmez.

`zarif_benchmark_start` tıpkı `zarif_propose_edit` gibi anında döner (henüz
sampling yapmaz):

| Parametre | Tip | Varsayılan | Açıklama |
|---|---|---|---|
| `editor_model` | `string \| null` | `null` | Verilirse sampling yerine bu sağlayıcı kullanılır. |
| `max_attempts` | `integer` | `2` | Her dosya için `verify_retention` başarısız olursa kaç kez yeniden denenir. |

```jsonc
{ "run_id": "3f1c...-uuid", "status": "in_progress", "file": "agent-instructions-1.md", "file_index": 1, "total_files": 5, "completed": [] }
```

`zarif_benchmark_continue(run_id)` her çağrıda **en fazla bir** sampling
round-trip'i yapar; `status` `"in_progress"` olduğu sürece tekrar tekrar
çağırın. Bir dosya bitince `completed` listesine eklenir, otomatik olarak
sıradaki dosyaya geçilir. Tüm dosyalar bitince:

```jsonc
{
  "run_id": "3f1c...-uuid",
  "status": "done",
  "results": [
    { "name": "agent-instructions-1.md", "tokens": { "before": 1200, "after": 410, "reduction_pct": 65.8 }, "sufficient": true },
    ...
  ]
}
```

`zarif_benchmark_discard(run_id)` yarım kalmış bir ölçümü bellekten temizler
(`zarif_discard_edit` ile aynı mantık). **Not**: aşağı akış görev skorlaması
(CLI'daki `--downstream` bayrağı) şu an bu MCP araçlarında yok — sadece token
azalması ve bilgi korunumu (`sufficient`) ölçülür.

## 6. Uçtan uca örnek akış

Host'unuzdaki sohbette (ör. "CLAUDE.md dosyasını sadeleştir" dediğinizde
ajan şu adımları izler):

1. Ajan `zarif_propose_edit(file_path="CLAUDE.md")` çağırır (anında döner,
   `status: "in_progress"`).
2. Ajan `status` `"ready_to_apply"` olana kadar `zarif_continue_edit(session_id="...")`'i
   tekrar tekrar çağırır; her çağrı sizin sohbet ettiğiniz modele tek bir
   sampling isteği gönderir (host bunu sizin adınıza, genelde sessizce veya
   bir onay istemiyle yürütür).
3. Ajan size diff'i gösterir, "bu değişiklikleri uygulayayım mı?" diye sorar.
4. Onaylarsanız ajan `zarif_apply_edit(session_id="...")` çağırır, dosya
   yedeklenip yazılır. Reddederseniz ajan `zarif_discard_edit(session_id="...")`
   çağırıp session'ı temizler.
5. Yazılan sonuçtan memnun kalmazsanız `zarif_restore_edit(file_path="CLAUDE.md")`
   ile bir önceki haline geri dönebilirsiniz.

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
  (bkz. `io_utils.make_backup`); `zarif_restore_edit` bu yedeği geri
  yüklemek için kullanılabilir.
- Reddedilen öneriler için `zarif_discard_edit` çağrılmazsa session bellekte
  kalır — uzun süre çalışan bir sunucuda çok sayıda reddedilen öneri birikimli
  bellek kullanımına yol açabilir (bkz. Bölüm 10).
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
| `MCP error -32603: ... Another MCP sampling request is already in progress` (sık IntelliJ'de) | Zarif bu hatayı otomatik olarak sınırlı sayıda (5) üstel geri çekilmeyle yeniden dener (bkz. Bölüm 8). Ayrıca `zarif_propose_edit`/`zarif_continue_edit` artık her çağrıda en fazla bir sampling round-trip'i yaptığından (bkz. §4 IntelliJ notu), nested-sampling çakışma riski eskisinden çok daha düşüktür. Yine de görürseniz, host'un sampling/tool-timeout ayarlarını kontrol edin; son çare olarak `editor_model` ile tamamen atlanabilir (bkz. Bölüm 7), ama bu host'un modelini kullanma amacından vazgeçer. |
| `MCP error -32001: Request timed out` (IntelliJ'de `zarif_propose_edit` çağrısında) | Eski tasarımda zarif tek çağrıda 3-5 ardışık sampling round-trip'i yapıyordu; büyük dosyalarda bu host'un tool-call zaman aşımını aşabiliyordu. MCP progress notification denendi ama IntelliJ'in timeout'unu uzatmadığı doğrulandı. Çözüm: zarif artık adım adım ilerliyor — `zarif_propose_edit` anında döner, `zarif_continue_edit(session_id)` her çağrıda en fazla bir round-trip yapar, `status: "ready_to_apply"` olana kadar tekrar çağrılır (bkz. Bölüm 5). Yine de zaman aşımı yaşarsanız (çok yavaş bir host modeli ile tek round-trip bile uzun sürüyorsa), host'unuzda tool-call/timeout ayarı var mı kontrol edin. |

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
- `zarif_discard_edit` çağrılmayan session'lar, sunucu süreci sonlanana kadar
  bellekte kalır — otomatik bir zaman aşımı/temizlik mekanizması yok.
