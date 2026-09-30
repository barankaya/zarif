# Zarif — Açık Kaynak Spesifikasyonu (v3)
 
Bu doküman, Zarif'in açık kaynak bir proje olarak yeniden konumlandırılmış halidir. v2'deki editör agent fikri korunuyor. Değişen şey: kurumsal bir ortama veya belirli bir araç zincirine (Jira, Confluence vb.) bağımlılık kalktı, proje herkesin kullanabileceği genel bir araç olarak tasarlanıyor.
 
---
 
## 1. Proje Nedir
 
Zarif, agent'lara verilen markdown talimat dosyalarını (SKILL.md, CLAUDE.md, AGENTS.md, spec dosyaları, solution overview gibi teknik dokümanlar) okuyup, anlamı koruyarak daha kısa ve daha net hale getiren bir CLI aracıdır.
 
```
zarif edit CLAUDE.md
→ önerilen değişiklikleri diff olarak gösterir
→ sen onaylarsan dosyaya yazar
```
 
### Neden Var
 
Talimat dosyaları zamanla şişiyor: tekrarlar, dolgu cümleleri, ortada kalmış kritik kurallar. Bunun iki bedeli var.
 
1. Her oturumda okunan dosya, her seferinde token yakıyor.
2. Uzun ve gevşek bir dosya, modelin kuralları doğru uygulamasını zorlaştırıyor. Uzun context'te doğruluk düşüyor, bilgi ortada kalınca dikkat azalıyor.
 
Ek bir uyarı da var: LLM'in kendi yazdığı talimat dosyalarının bazı test ortamlarında görev başarısını düşürdüğünü gösteren çalışmalar var. Yani sorun sadece "çok uzun" değil, "gereğinden genel ve dolgulu" olması.
 
### Ayrıştırıcı Özellik
 
```
Headroom / LLMLingua gibi araçlar:
→ çalışma anında (runtime), mekanik sıkıştırma
→ tool çıktısı, log, JSON için güçlü
→ içeriği anlamadan token eler
 
Zarif:
→ çalışma öncesi (design-time), semantik düzenleme
→ insan yazımı talimat ve doküman dosyaları için
→ neyin kritik olduğunu anlayarak düzenler
→ sonucu insan onayına sunar, dosyayı sessizce değiştirmez
```
 
---
 
## 2. Kararlaştırılan Teknik Seçimler
 
```
Dil          → Python (öneri, gerekçeler aşağıda)
Çatı         → LangGraph
Arayüz       → CLI ile başla, sonra kütüphane ve MCP server
Kapsam       → agent'a verilen her markdown talimat dosyası
Model        → sağlayıcıdan bağımsız (yerel ve API modelleri)
```
 
### Neden Python
 
LangGraph'ın doküman ve örneklerinin çoğu Python-öncelikli, structured output için Pydantic doğal olarak orada, checkpointer seçenekleri (SQLite, Postgres) olgun. CLI için Typer veya Click yeterli, dağıtım pip/pipx ile basit. Not: bu değerlendirmeyi kodlamaya başlamadan önce güncel LangGraph sürümü üzerinde bir kez doğrulamak iyi olur.
 
---
 
## 3. Tasarım Prensipleri
 
### Prensip 1 — Sadece Çıkar, Taşı, Sadeleştir; Yeni Bilgi Ekleme
Editör agent bir LLM olduğu için kendi kafasından genel geçer cümleler ekleyebilir, bu da tam çözmeye çalıştığımız sorunu yaratır. Bu yüzden Zarif yalnızca şunları yapabilir: gereksiz tekrarı silmek, cümleyi kısaltmak, kritik maddeyi başa veya sona taşımak. Orijinalde olmayan bir iddia eklemesi yasak ve bu, doğrulama adımında kontrol edilir.
 
### Prensip 2 — Üreten ile Doğrulayan Ayrı
Düzenlemeyi yapan node ile bilgi kaybını kontrol eden node ayrı çalışır, ayrı prompt ve mümkünse ayrı model kullanır.
 
### Prensip 3 — İnsan Onayı Olmadan Yazma Yok
Zarif dosyayı doğrudan değiştirmez. Önce diff üretir, onay bekler. Orijinal dosya yedeklenir.
 
### Prensip 4 — Ölçülebilir Olmak
Bir "kalite arttı" iddiası ölçülmeden yayınlanmaz. Ölçüm altyapısı projenin ilk günden bir parçasıdır (bkz. Bölüm 5).
 
### Prensip 5 — Dosya Bütünlüğü
Encoding korunur (özellikle BOM), satır sonları korunur, kod blokları ve tablolar yapısal olarak bozulmaz. Agent'ların dosya düzenlerken encoding'i sessizce bozabildiği bilinen bir sorun.
 
---
 
## 4. LangGraph Mimarisi (Taslak)
 
```
[parse]
  Markdown'ı bölümlere ayır, kod bloklarını ve tabloları işaretle
    ↓
[extract_critical]
  Structured output: kritik maddelerin listesi
  (kural, sınır, kabul kriteri, uyarı)
    ↓
[propose_edit]
  Sadece çıkar / taşı / sadeleştir kuralıyla düzenlenmiş sürüm
    ↓
[verify_retention]  ← ayrı node, ayrı prompt
  Structured output: hangi kritik madde korunmuş, hangisi kaybolmuş,
  yeni iddia eklenmiş mi
    ↓
  koşullu geçiş
   ├─ yeterli            → [human_review]
   ├─ kayıp var, hak var → [propose_edit] (kayıp listesiyle tekrar)
   └─ deneme limiti doldu → [human_review] (en iyi sonuç + uyarı notu)
    ↓
[human_review]  ← interrupt_before, diff gösterilir
    ↓
[write]  ← yedek alır, encoding'i koruyarak yazar
```
 
### Serideki Hangi Desen Nerede
 
```
Evaluator-optimizer      → propose_edit ile verify_retention döngüsü
Structured output        → extract_critical ve verify_retention çıktıları
Kendi tur sayacı         → deneme limiti ve graceful exit
Checkpointer (SQLite)    → uzun dosyalarda kesinti toleransı
Human-in-the-loop        → onay noktası
Reducer                  → bölüm bazlı paralel işlemede sonuç birleştirme
```
 
Bölümler birbirinden bağımsızsa paralel işlenebilir (paralel ayrıştırma), ama kritik maddeler dosya genelinde çapraz referans verebildiği için ilk sürümde sıralı başlamak daha güvenli.
 
---
 
## 5. Ölçüm Yaklaşımı
 
Üç ölçüt, üçü de proje içinde çalıştırılabilir bir benchmark olarak yayınlanır.
 
```
1. Token azalması
   Öncesi ve sonrası token sayısı. En kolay ölçüt ama tek başına yetersiz.
 
2. Bilgi korunumu
   Orijinal dosyadan çıkarılan kritik maddelerin kaçı düzenlenmiş
   dosyada hâlâ var. Hedef: kritik maddelerde kayıp sıfıra yakın.
 
3. Aşağı akış performansı (en değerli)
   Aynı görevi bir agent'a iki kez yaptır: ham dosyayla ve
   düzenlenmiş dosyayla. Hangisi daha az hata yapıyor, daha az
   token harcıyor. "Sıkıştırma kaliteyi düşürmüyor" iddiasını
   gerçekten sınayan tek ölçüt bu.
```
 
Ölçüm sonuçları README'de dürüstçe yayınlanır. Zarif'in işe yaramadığı durumlar da (örneğin zaten kısa ve net dosyalar) belgelenir.
 
---
 
## 6. v0.1 Kapsamı
 
### İçeride
```
- zarif edit <dosya.md> komutu
- parse, extract_critical, propose_edit, verify_retention, human_review, write
- Diff çıktısı ve onay akışı
- Yedek alma, encoding koruma
- Sağlayıcıdan bağımsız model arayüzü (yerel ve API)
- Token ölçümü (öncesi/sonrası)
- Örnek benchmark seti: 5 talimat dosyası, her biri için aşağı akış görevi
  (bkz. src/zarif/benchmark/samples/, src/zarif/benchmark/downstream.py)
- MCP server modu (zarif serve) — planlanandan erken öne çekildi, bkz. aşağı
```

### Bilinçli Olarak Dışarıda
```
- Kütüphane API'si               → v0.2
- Toplu / klasör işleme          → v0.2
- Markdown dışı formatlar        → sonra
```

**Not (2026-09-30):** MCP server modu orijinal planda v0.3 içindi; "zarif'i
bağımsız bir MCP sunucusuna çevirip hangi ajanla çalıştırılırsa onun
modelini kullanma" fikri erken değerli görüldüğü için v0.1 kapsamına
alındı ve uygulandı (bkz. [`src/zarif/mcp_server.py`](src/zarif/mcp_server.py),
kullanım kılavuzu [`docs/mcp-guide.md`](docs/mcp-guide.md)). "Otomatik
(onaysız) mod düşünülmüyor" maddesi kaldırıldı çünkü §7'de belgelenen
`--yes` bayrağıyla kısmen çelişiyordu — gerçek karar §7'de netleştirildi.

---

## 7. Kararlaştırıldı

- [x] **Lisans: Apache-2.0.** Patent grant içermesi, kurumsal katkıya daha güvenceli olması nedeniyle MIT yerine tercih edildi. `LICENSE` dosyası repo kökünde.
- [x] **Paket/repo adı: "zarif" ile devam.** PyPI'de bu isim kayıtlı değil (JSON API 404). GitHub'da repo adları `kullanıcı/repo` şeklinde namespace'li olduğundan aynı isimde başka (alakasız) repoların var olması engel değil.
- [x] **Varsayılan yerel model: `ollama:llama3.1`.** Tool-calling/structured output desteği iyi olduğu için başlangıç noktası olarak seçildi. Gerçek kalite/hız ölçümü yapılıp gerekirse değiştirilecek — `--editor-model`/`--verifier-model` ile her zaman override edilebilir.
- [x] **Benchmark görevi: hem kural uyma simülasyonu hem sabit soru-cevap seti.** Görev seti `src/zarif/benchmark/downstream.py`'de uygulandı (5 örnek dosyanın her biri için 2-3 soru + 2 kural-tetikleyici senaryo, anahtar kelime eşleşmesiyle puanlanıyor). `zarif benchmark --downstream` ile ham/düzenlenmiş dosya karşılaştırması çalıştırılabilir. Görev seti zamanla genişletilecek.
- [x] **Editör/doğrulayıcı model politikası: mevcut esnek davranış korunuyor.** `--verifier-model` boş bırakılırsa `--editor-model` ile aynısı kullanılır; farklı model istenirse açıkça belirtilir. Zorunlu farklı model şartı getirilmedi (kurulumu zorlaştırmamak için).
- [x] **MCP server modu v0.1'e alındı, model sağlayıcı olarak GitHub Copilot SDK kullanılmıyor.** Zarif ayrıca bağımsız bir GitHub Copilot SDK entegrasyonu (Node.js/CLI login gerektiren) ile denendi, sonra bilinçli olarak kaldırıldı — bunun yerine "zarif hangi MCP host'ta çalıştırılırsa onun modelini MCP sampling ile kullanır" yaklaşımı benimsendi (bkz. `src/zarif/mcp_server.py`, `docs/mcp-guide.md`). Böylece host-bağımsız tek bir mekanizma var, sağlayıcıya özel SDK entegrasyonu yok.
- [x] **`zarif edit --yes/-y` bayrağı kalıcı olarak korunuyor, Prensip 3'ü ihlal etmiyor.** Bu bayrak yalnızca interaktif "yazayım mı?" onayını atlar; `human_review` node'u yine çalışır, diff yine üretilir/gösterilir/loglanır ve `write` node'undan önce hâlâ bir yedek alınır. Amacı otomasyon/CI senaryolarında insan gözden geçirmesini ortadan kaldırmak değil, terminalin interaktif olmadığı durumlarda akışı tıkamamaktır — "sessiz, onaysız mod" (dosyanın hiç gösterilmeden değiştirilmesi) hâlâ desteklenmiyor ve düşünülmüyor.

---

## 8. Sonraki Adımlar

```
1. Açık kararları netleştir                              → tamam (§7)
2. Örnek veri seti hazırla: 5 gerçek talimat dosyası       → tamam (src/zarif/benchmark/samples/)
3. En basit uçtan uca akışı kur: parse → propose → verify → diff  → tamam
4. Ölçümü baştan çalıştır, ilk sayıları gör                → AÇIK — bu depo, model/ağ
   erişimi olmayan bir ortamda geliştirildi; `zarif benchmark --downstream`
   gerçek bir model (ör. yerel Ollama) ile hiç çalıştırılmadı. Kullanıcının
   kendi makinesinde `pip install -e ".[dev,local]"` + `zarif benchmark
   --editor-model ollama:llama3.1 --downstream` çalıştırıp sonuçları
   README'ye eklemesi gerekiyor.
5. Sayılar umut vericiyse README'yi yaz ve yayınla         → AÇIK — README
   zaten "nasıl çalıştırılır"ı anlatıyor ama gerçek ölçüm sayıları henüz
   yok (madde 4'e bağımlı). Depo henüz bir git repository'si değil / hiçbir
   yere push edilmedi — "yayınla" adımı bunu da kapsıyor.
```

---

*Bu doküman canlı bağlamdır. Her önemli karar burada güncellenmeli.*