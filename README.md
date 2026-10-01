# Zarif

Agent'lara verilen markdown talimat dosyalarını (`SKILL.md`, `CLAUDE.md`,
`AGENTS.md`, spec dosyaları vb.) okuyup, anlamı koruyarak daha kısa ve daha
net hale getiren bir CLI aracı.

```
zarif edit CLAUDE.md
→ önerilen değişiklikleri diff olarak gösterir
→ onaylarsan dosyaya yazar (öncesinde yedek alır)
```

Tam motivasyon ve tasarım kararları için [spec.md](spec.md) dosyasına bakın.
Bu README, v0.1 iskeletinin ne durumda olduğunu anlatır.

## Durum: v0.1 iskeleti

Aşağıdaki uçtan uca akış çalışır durumda:

```
parse → extract_critical → propose_edit → verify_retention
                                 ↑                │
                                 └── (kayıp varsa, hak varsa) ──┘
                                                  │
                                          human_review → write
```

- **parse**: kod bloklarını ve tabloları placeholder token'larla korumaya alır.
- **extract_critical**: kritik maddeleri (kural, sınır, kabul kriteri, uyarı)
  structured output ile çıkarır.
- **propose_edit**: sadece çıkar/taşı/sadeleştir kuralıyla düzenlenmiş sürüm üretir.
- **verify_retention**: ayrı bir prompt/model ile hangi kritik maddenin
  korunduğunu, hangisinin kaybolduğunu ve yeni iddia eklenip eklenmediğini
  kontrol eder. Yetersizse ve deneme hakkı varsa `propose_edit`'e geri döner.
- **human_review**: diff'i terminalde renkli gösterir, uyarıları listeler.
- **write**: onay sonrası yedek alır, orijinal encoding/satır sonunu koruyarak yazar.

Bu akış [LangGraph](https://github.com/langchain-ai/langgraph) ile kurulu; onay
noktası `interrupt_before=["write"]` ile sağlanıyor, yani `write` node'u insan
onayı gelmeden hiç çalışmıyor.

## Hızlı başlangıç

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,local,mcp]"
pytest
```

Bu adımlar internet erişimi gerektirir (PyPI'den paket indirir). Kurulum
bitince ya doğrudan CLI'yi (`zarif edit ...`, aşağıya bakın) ya da bir MCP
sunucusu olarak (`zarif serve`, ayrıntılı kılavuz:
[docs/mcp-guide.md](docs/mcp-guide.md)) çalıştırabilirsiniz.

## Kurulum

```powershell
pip install -e ".[dev]"
# Yerel model (Ollama) kullanacaksanız:
pip install -e ".[local]"
# OpenAI-uyumlu bir API kullanacaksanız:
pip install -e ".[api]"
```

## Kullanım

```powershell
zarif edit CLAUDE.md --editor-model ollama:llama3.1
zarif edit CLAUDE.md --editor-model openai:gpt-4o-mini --verifier-model ollama:llama3.1
```

Model spesifikasyonu `provider:model_adı` biçiminde; şu an `ollama` ve `openai`
sağlayıcıları destekleniyor (bkz. [`src/zarif/models.py`](src/zarif/models.py)).
Bağımsız bir model kurulumuna hiç gerek duymadan kullanmak için aşağıdaki
MCP sunucusu modunu tercih edin.

## MCP sunucusu

```powershell
pip install -e ".[mcp]"
zarif serve
```

Zarif, bağımsız bir MCP (Model Context Protocol) sunucusudur; hangi ajanla
(VS Code Copilot Chat, Claude Desktop, başka bir MCP host — fark etmez)
çalıştırılırsa, editör/doğrulayıcı LLM çağrılarını MCP'nin "sampling"
yeteneğiyle o ajanın kendi modeline devreder — zarif'in ayrıca bir
API anahtarına/yerel model kurulumuna ihtiyacı olmaz. `zarif_propose_edit`,
`zarif_apply_edit`, `zarif_discard_edit` ve `zarif_restore_edit` araçlarını sunar.

Host konfigürasyonu, tam araç referansı, örnek istek/yanıtlar ve sorun
giderme için: **[docs/mcp-guide.md](docs/mcp-guide.md)**.

**Doğrulanamayan nokta:** bu ortamda ağ erişimi olmadığı için `mcp` paketinin
sampling API'sinin (`SamplingMessage`, `ctx.session.create_message(...)`)
tam imzası kurulu bir paket karşısında test edilmedi; resmi
[MCP Python SDK](https://modelcontextprotocol.io/) dokümanına göre yazıldı.

## Yedeği geri yükleme

`zarif edit` her yazmadan önce orijinal dosyayı `<dosya>.bak` (veya çakışma
varsa `.bak1`, `.bak2`, ...) olarak yedekler. Son yapılan değişikliği geri
almak için:

```powershell
zarif restore CLAUDE.md
```

En son alınan yedeği bulup onay sonrası dosyanın üzerine yazar; `--yes`/`-y`
ile onay adımı atlanabilir.

## Ölçüm (benchmark)

Spec'teki 3 ölçütten ilk ikisi (token azalması, bilgi korunumu) gömülü
örnekler üzerinde hiçbir dosyaya yazmadan çalıştırılabilir:

```powershell
zarif benchmark --editor-model ollama:llama3.1
```

Üçüncü ölçüt (aşağı akış performansı) `--downstream` bayrağıyla eklenir —
gömülü örnek dosyalar için tanımlı soru-cevap + senaryo setini hem ham hem
düzenlenmiş metinle çalıştırıp karşılaştırır (bkz.
[`src/zarif/benchmark/downstream.py`](src/zarif/benchmark/downstream.py)):

```powershell
zarif benchmark --editor-model ollama:llama3.1 --downstream
```

Sonucu bir script/CI adımında işlemek isterseniz `--json` ile makine-okunur
çıktı alabilirsiniz (insan-okunur tablo yerine):

```powershell
zarif benchmark --editor-model ollama:llama3.1 --downstream --json > sonuc.json
```

## Pano (dashboard)

Üç ölçütün tümünü (token azalması, bilgi korunumu, aşağı akış performansı)
tek bir terminal tablosunda ve paylaşılabilir bir HTML dosyasında birleştirir:

```powershell
zarif dashboard --editor-model ollama:llama3.1 --out dashboard.html
```

`--downstream` her zaman açıktır (panonun amacı tüm ölçütleri göstermek),
bu yüzden ekstra bir "agent" model çağrısı gerektirir — `--agent-model` ile
farklı bir model seçilebilir. Üretilen `dashboard.html` tarayıcıda açılabilir,
harici bir CDN/JS bağımlılığı yoktur.

## Hiyerarşi analizi

Bir dizin altındaki tüm talimat dosyalarını (`CLAUDE.md`, `AGENTS.md`,
`SKILL.md`, `.cursorrules`, `*copilot-instructions.md`, vb.) keşfeder, her
birinin token sayısını/derinliğini raporlar ve dosyalar arası tekrar eden
paragrafları bulur (daha sığ/genel dosyada tutulup daha derin/özel dosyadan
çıkarılması önerilir). LLM veya ağ erişimi gerektirmez — saf metin
karşılaştırmasıdır:

```powershell
zarif hierarchy .
zarif hierarchy . --json
```

## Denetim (audit)

Tek bir talimat dosyasını üç deterministik/heuristik kontrolden geçirir:

- **Ölü kural tespiti**: kural zaten `.editorconfig`/`.prettierrc`/
  `tsconfig.json`/`pyproject.toml` gibi bir konfigürasyon dosyası tarafından
  uygulanıyorsa işaretlenir.
- **Secret/PII taraması**: AWS anahtarı, private key, Slack token, jenerik
  secret/password ataması gibi desenleri regex ile arar (yer tutucular —
  `your-api-key`, `changeme` vb. — hariç tutulur, bulgular redakte edilir).
- **Çelişki tespiti (deneysel)**: zıt kutuplu ("always"/"never" gibi) ve
  ortak kelime içeren kural çiftlerini sezgisel olarak işaretler; yanlış
  pozitif verebilir.

```powershell
zarif audit CLAUDE.md
zarif audit CLAUDE.md --project-root . --json
zarif audit CLAUDE.md --sarif zarif-audit.sarif.json   # CI/GitHub Code Scanning
```

## Testler

```powershell
pytest
```

## Proje yapısı

```
src/zarif/
  cli.py             Typer CLI: `edit`, `restore`, `hierarchy`, `audit`, `benchmark`, `dashboard`, `serve` komutları
  io_utils.py        Encoding/satır sonu koruma, yedekleme, kod/tablo koruma
  diffing.py         Unified diff üretimi ve renkli terminal çıktısı
  tokens.py          Token sayımı (tiktoken + heuristik fallback)
  models.py          Sağlayıcıdan bağımsız chat model factory (ollama/openai)
  mcp_server.py      MCP sunucusu: sampling tabanlı model, propose/apply/discard/restore araçları
  hierarchy.py       Çoklu dosya keşfi + dosyalar arası tekrar eden paragraf tespiti (LLM'siz)
  audit.py           Ölü kural / secret-PII / çelişki tespiti + SARIF çıktısı (LLM'siz)
  graph/              LangGraph düğümleri, prompt'lar, structured output şemaları
  benchmark/samples/  Örnek (bilinçli şişirilmiş) talimat dosyaları (5 dosya)
  benchmark/dashboard.py  Tüm ölçütleri birleştiren HTML pano render'ı
docs/                MCP kullanım kılavuzu, LinkedIn paylaşım serisi
tests/                pytest testleri (model çağrısı gerektirmeyen kısımlar)
```

## Kararlaştırılan kararlar

spec.md §7'de listelenen kararlar netleşti: lisans **Apache-2.0** (bkz.
[`LICENSE`](LICENSE)), paket/repo adı **"zarif"** ile devam ediyor (PyPI'de boş,
GitHub'da namespace çakışması yok), varsayılan yerel model **`ollama:llama3.1`**
(gerçek ölçüm sonrası değişebilir), editor/verifier için varsayılan davranış
esnek kaldı (aynı model, istenirse `--verifier-model` ile farklısı seçilir).
Benchmark'ın 3. ölçütü (aşağı akış performansı) için hem "kural uyma
simülasyonu" hem "sabit soru-cevap seti" yaklaşımı benimsendi ve
`src/zarif/benchmark/downstream.py` içinde uygulandı — `zarif benchmark
--downstream` ile ham/düzenlenmiş dosya karşılaştırması çalıştırılabilir.
Görev seti (şu an gömülü 5 örnek dosyanın her biri için 2-3 soru + 2 senaryo)
zamanla genişletilecek. MCP server modu, spec'te v0.3 için planlanmışken
erken öne çekilip v0.1 kapsamına alındı; ayrıca denenip kaldırılan bir
GitHub Copilot SDK entegrasyonu yerine host-bağımsız MCP sampling yaklaşımı
benimsendi (bkz. spec.md §7, [docs/mcp-guide.md](docs/mcp-guide.md)).

## Bilinen açık noktalar

- **Gerçek ölçüm sonuçları henüz yok.** `zarif benchmark --downstream` bu
  depoda hiç gerçek bir modelle (ağ/model erişimi olmayan bir ortamda
  geliştirildi) çalıştırılmadı. Kendi ortamınızda çalıştırıp sonuçları bu
  bölüme eklemeniz faydalı olur.
- Kütüphane API'si ve toplu/klasör işleme henüz yok (spec.md §6, v0.2 kapsamı).
- `mcp` paketinin sampling API imzası kurulu bir paket karşısında test
  edilmedi (bkz. [docs/mcp-guide.md](docs/mcp-guide.md) §10).
