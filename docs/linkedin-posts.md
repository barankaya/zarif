# Zarif — LinkedIn Paylaşım Serisi

Bu dosya, Zarif projesinin geliştirme aşamaları ilerledikçe paylaşılacak
LinkedIn postlarını biriktirir. Her aşama tamamlandıkça yeni bir "Post N"
bölümü eklenecek; bu ilk post genel tanıtım amaçlı.

---

## Post 1 — Tanıtım

AI ajanlarına (Claude, Copilot, kendi agent'larınız...) talimat vermek için
`CLAUDE.md`, `AGENTS.md`, `SKILL.md` gibi markdown dosyaları yazıyoruz.
Zaman içinde bu dosyalar şişiyor: tekrar eden açıklamalar, gereksiz örnekler,
"emin olmak için" eklenen fazladan cümleler...

Sonuç: her ajan çağrısında daha fazla token, daha yavaş yanıt, daha yüksek
maliyet — ve çoğu zaman modelin asıl kritik kuralı bulması daha da zorlaşıyor.

Bunu çözmek için **Zarif**'i geliştiriyorum: agent'lara verilen markdown
talimat dosyalarını **anlamı koruyarak** sadeleştiren bir araç.

**Nasıl çalışıyor?**

1. **Parse** — kod bloklarını ve tabloları koruma altına alır (bunlara
   dokunulmaz).
2. **Kritik madde çıkarımı** — kural, sınır, kabul kriteri ve uyarıları ayrı
   ayrı tespit eder.
3. **Sadeleştirme** — yalnızca "çıkar / taşı / sadeleştir" ilkesiyle metni
   kısaltır, yeni bilgi eklemez.
4. **Doğrulama** — ayrı bir model/prompt, hiçbir kritik maddenin
   kaybolmadığını ve yeni bir iddia eklenmediğini kontrol eder; yetersizse
   otomatik olarak yeniden dener.
5. **İnsan onayı** — değişiklik diff olarak gösterilir, siz onaylamadan
   hiçbir dosyaya yazma işlemi gerçekleşmez.

Teknik olarak bu akış [LangGraph](https://github.com/langchain-ai/langgraph)
ile modelleniyor; model sağlayıcı bağımsız (yerel Ollama, OpenAI-uyumlu API
ya da bağlı olduğunuz MCP host'un kendi modeli üzerinden çalışabiliyor).

Zarif'i aynı zamanda bağımsız bir **MCP (Model Context Protocol) sunucusu**
olarak da çalıştırabiliyorsunuz — VS Code, Claude Desktop veya başka bir MCP
uyumlu istemciye bağlayıp, ekstra bir model/API anahtarı kurulumu yapmadan
doğrudan kullanabiliyorsunuz.

Proje henüz v0.1 aşamasında; ilerleyen adımları (ölçüm sonuçları, MCP
entegrasyonu, gerçek dosyalar üzerindeki token azalması) bu seri içinde
paylaşmaya devam edeceğim.

#YapayZeka #AIAgents #LLM #DeveloperTools #MCP #LangGraph #OpenSource

---

## Post 2 — MCP entegrasyonu tamamlandı

İlk paylaşımda söz verdiğim MCP (Model Context Protocol) entegrasyonu
Zarif'te artık tam çalışır durumda — ve planlanandan erken (v0.3 yerine
v0.1'e çekerek) tamamlandı.

**Neden önemli?** Zarif'i bağımsız bir model kurulumuna (Ollama, API
anahtarı vb.) hiç ihtiyaç duymadan kullanabiliyorsunuz artık. VS Code
Copilot Chat, Claude Desktop ya da başka bir MCP host'a bağladığınızda,
Zarif editör/doğrulayıcı adımları için MCP'nin **sampling** yeteneğiyle
doğrudan o host'un kendi modelini kullanıyor.

Sunulan 4 araç, tam bir yaşam döngüsü kapsıyor:

- `zarif_propose_edit` — sadeleştirme önerisi üretir, diff döner
- `zarif_apply_edit` — onaylanan öneriyi yedek alarak dosyaya yazar
- `zarif_discard_edit` — reddedilen bir öneriyi bellekten temizler
- `zarif_restore_edit` — en son yedekten geri yükler (CLI'daki `zarif
  restore` ile aynı mekanizma, MCP tarafında da erişilebilir)

Bu arada CLI tarafına da iki komut eklendi: `zarif restore` (son yedeği
geri yükler) ve `zarif dashboard` (token azalması, bilgi korunumu ve aşağı
akış performansı ölçütlerinin tümünü tek bir HTML panosunda birleştirir).
Benchmark seti de 5 gerçekçi (bilinçli şişirilmiş) örnek talimat dosyasına
çıkarıldı.

Detaylı kurulum ve host konfigürasyonu için: [docs/mcp-guide.md](../docs/mcp-guide.md).

Sırada: gerçek bir modelle çalıştırılmış ölçüm sonuçları — şimdiye kadar
geliştirme tamamen ağ erişimi olmayan bir ortamda yapıldı, bu yüzden
sadece statik/sözdizimsel doğrulama mümkündü. Bir sonraki post, gerçek
sayılarla gelecek.

#YapayZeka #AIAgents #LLM #DeveloperTools #MCP #LangGraph #OpenSource

---

### Kaynaklar

- LangGraph — <https://github.com/langchain-ai/langgraph>
- Model Context Protocol (MCP) — <https://modelcontextprotocol.io/>
