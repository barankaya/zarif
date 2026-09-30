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

### Kaynaklar

- LangGraph — <https://github.com/langchain-ai/langgraph>
- Model Context Protocol (MCP) — <https://modelcontextprotocol.io/>
