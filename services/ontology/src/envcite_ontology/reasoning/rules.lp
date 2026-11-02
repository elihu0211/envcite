% 通用推論程式：法條不寫成程式，陳述是資料（事實輸入）。數值一律為千分位整數。
% 事實由 facts.py 自資料庫產生，案件事實由呼叫端提供。
% 輸入：statement/3 cond/7 thr/2 thr_src/2 delegated/1 exception/2 override/3 sanction/5 action/2 src/2（由 PG 依 as-of 日期產生）
%       vcond/2（與概念值比較的要件原子）  stmt_law/2（陳述所屬法規）  defmember/3（法規內用語定義涵蓋的成員）
%       defsrc/4（同上，另帶定義編號，供依據軌跡）
%       cfact_in/2（案件的概念值事實：概念代碼 → 值概念代碼）
%       judgement/1（需判斷的要件原子）  repeating/1（按次處罰的罰則）
%       permit_cond/2（委由個別處分的要件：許可種類）  lim_method/2 lim_avg/2 lim_unlabeled/1（授權限值的認定方式）
%       trigger/3（罰則 → 作為前提的罰則，觸發種類；只列當日在的前提）  triggered/1（有前提的罰則）  same/2（對到同一外部識別碼的概念：前者的事實當成後者的）
%       fact_in/2（案件事實，由呼叫端提供；同一概念可帶多個值，矛盾由下方檢出）
%       subject_basis/2 subject_any/1（罰則主體槽位標的責任基礎）  actor_basis/1（呼叫端說明的行為人責任基礎）
%       fmethod/2 favg/2（案件事實的認定方式）  permit_in/3（呼叫端提供的許可量值：許可種類、概念、值）
role(scope;duty).

% ---- 等同的概念：對到同一個外部識別碼（只記等同，不記上下位）；事實與認定方式照搬 ----
fact_in(C,V)  :- fact_in(D,V), same(D,C).
cfact_in(C,V) :- cfact_in(D,V), same(D,C).
fmethod(C,X)  :- fmethod(D,X), same(D,C).
favg(C,X)     :- favg(D,X), same(D,C).
permit_in(K,C,V) :- permit_in(K,D,V), same(D,C).
permit_in(L,M,V) :- permit_in(K,M,V), same(K,L).

% ---- 矛盾事實：同一概念有兩個不同的值，該概念視同沒有可用的值，並列為問題 ----
% 不擇一、不取平均：哪一個值對，是事實認定，不是推論。矛盾的概念不冒充「缺事實」，
% 另列 problem；只有因它而未定的結論受影響，與它無關的結論照常輸出。
conflicting(M) :- fact_in(M,V1), fact_in(M,V2), V1 < V2.
fact(M,V)      :- fact_in(M,V), not conflicting(M).
problem(contradictory_fact, M) :- conflicting(M).
% ---- 概念值事實：概念取某個值概念（行業屬某行業）；矛盾同樣不擇一 ----
conflicting_c(M) :- cfact_in(M,V1), cfact_in(M,V2), V1 < V2.
cfact(M,V)       :- cfact_in(M,V), not conflicting_c(M).
problem(contradictory_fact, M) :- conflicting_c(M).
given(M) :- fact_in(M,_).
given(M) :- cfact_in(M,_).

% 「甲屬於乙」的依據只有該法現行的用語定義（定義只及於本法）；詞彙沒有上下位可用。
isa(L,X,X) :- stmt_law(_,L), cfact_in(_,X).
isa(L,X,Y) :- isa(L,X,Z), same(Z,Y).
isa(L,X,T) :- defmember(L,T,Y), isa(L,X,Y).

% 依據軌跡：某個值之所以屬於比較的用語，所經過的每一則定義（可能是遞移的）。
contrib(L,X,T,D) :- defsrc(L,T,Y,D), isa(L,X,Y).
contrib(L,X,T,D) :- defsrc(L,T,Y,_), isa(L,X,Y), contrib(L,X,Y,D).
used_def(C,D)    :- vcond(C,V), cond(C,S,_,_,M,_,_), cfact(M,X), stmt_law(S,L),
                    isa(L,X,V), contrib(L,X,V,D).

prescriptive(S) :- statement(S,prescriptive,_).
deontic(S,D)    :- statement(S,prescriptive,D).

% ---- 委由個別處分：門檻值是呼叫端提供的許可量值；沒提供則要件未知，矛盾則不擇一 ----
conflicting_p(K,M) :- permit_in(K,M,V1), permit_in(K,M,V2), V1 < V2.
problem(contradictory_fact, K) :- conflicting_p(K,_).
permit_given(K,M) :- permit_in(K,M,_).
thr(C,T) :- permit_cond(C,K), cond(C,_,_,_,M,_,_), permit_in(K,M,T), not conflicting_p(K,M).

% ---- 認定方式：限值有標，事實的檢測方法或平均時段不同或沒給，就不以數值逕判 ----
meas_bad(C) :- lim_method(C,X), cond(C,_,_,_,M,_,_), not fmethod(M,X).
meas_bad(C) :- lim_method(C,X), cond(C,_,_,_,M,_,_), fmethod(M,Y), Y != X.
meas_bad(C) :- lim_avg(C,X), cond(C,_,_,_,M,_,_), not favg(M,X).
meas_bad(C) :- lim_avg(C,X), cond(C,_,_,_,M,_,_), favg(M,Y), Y != X.
cmp(C,T) :- thr(C,T), not meas_bad(C).

% ---- 要件原子的三值：t 成立、f 不成立、無（未知） ----
atom(C) :- cond(C,_,_,_,_,_,_).
raw(C,t) :- cond(C,_,_,_,M,le,_), cmp(C,T), fact(M,V), V <= T.
raw(C,f) :- cond(C,_,_,_,M,le,_), cmp(C,T), fact(M,V), V >  T.
raw(C,t) :- cond(C,_,_,_,M,lt,_), cmp(C,T), fact(M,V), V <  T.
raw(C,f) :- cond(C,_,_,_,M,lt,_), cmp(C,T), fact(M,V), V >= T.
raw(C,t) :- cond(C,_,_,_,M,ge,_), cmp(C,T), fact(M,V), V >= T.
raw(C,f) :- cond(C,_,_,_,M,ge,_), cmp(C,T), fact(M,V), V <  T.
raw(C,t) :- cond(C,_,_,_,M,gt,_), cmp(C,T), fact(M,V), V >  T.
raw(C,f) :- cond(C,_,_,_,M,gt,_), cmp(C,T), fact(M,V), V <= T.
raw(C,t) :- cond(C,_,_,_,M,eq,_), cmp(C,T), fact(M,V), V == T.
raw(C,f) :- cond(C,_,_,_,M,eq,_), cmp(C,T), fact(M,V), V != T.
raw(C,t) :- cond(C,_,_,_,M,ne,_), cmp(C,T), fact(M,V), V != T.
raw(C,f) :- cond(C,_,_,_,M,ne,_), cmp(C,T), fact(M,V), V == T.
raw(C,t) :- cond(C,S,_,_,M,eq,_), vcond(C,V), cfact(M,X), stmt_law(S,L), isa(L,X,V).
raw(C,f) :- cond(C,S,_,_,M,eq,_), vcond(C,V), cfact(M,X), stmt_law(S,L), not isa(L,X,V).
raw(C,t) :- cond(C,S,_,_,M,ne,_), vcond(C,V), cfact(M,X), stmt_law(S,L), not isa(L,X,V).
raw(C,f) :- cond(C,S,_,_,M,ne,_), vcond(C,V), cfact(M,X), stmt_law(S,L), isa(L,X,V).
v(C,t) :- raw(C,t), cond(C,_,_,_,_,_,0).
v(C,f) :- raw(C,f), cond(C,_,_,_,_,_,0).
v(C,t) :- raw(C,f), cond(C,_,_,_,_,_,1).
v(C,f) :- raw(C,t), cond(C,_,_,_,_,_,1).
unk(C)  :- atom(C), not v(C,t), not v(C,f).

% ---- DNF：clause 內為且，clause 之間為或 ----
clause(S,R,K)    :- cond(_,S,R,K,_,_,_).
clause_f(S,R,K)  :- cond(C,S,R,K,_,_,_), v(C,f).
clause_nt(S,R,K) :- cond(C,S,R,K,_,_,_), not v(C,t).
cv(S,R,K,t) :- clause(S,R,K), not clause_nt(S,R,K).
cv(S,R,K,f) :- clause_f(S,R,K).
cv(S,R,K,u) :- clause(S,R,K), not cv(S,R,K,t), not cv(S,R,K,f).
part(S,R)   :- clause(S,R,_).
pv_nf(S,R)  :- clause(S,R,K), not cv(S,R,K,f).
pv(S,R,t) :- cv(S,R,_,t).
pv(S,R,f) :- part(S,R), not pv_nf(S,R).
pv(S,R,u) :- part(S,R), not pv(S,R,t), not pv(S,R,f).
pv(S,R,t) :- statement(S,_,_), role(R), not part(S,R).   % 沒有該類要件即恆成立

% ---- 例外：但書成立則本文被排除（不論本文要件為何）；但書未知則附條件 ----
exc_t(S) :- exception(E,S), pv(E,scope,t).
exc_u(S) :- exception(E,S), pv(E,scope,u).
status(S,excluded)       :- prescriptive(S), exc_t(S).
status(S,not_applicable) :- prescriptive(S), pv(S,scope,f), not exc_t(S).
status(S,applies)        :- prescriptive(S), pv(S,scope,t), not exc_t(S), not exc_u(S).
status(S,conditional)    :- prescriptive(S), pv(S,scope,t), not exc_t(S), exc_u(S).
status(S,undetermined)   :- prescriptive(S), pv(S,scope,u), not exc_t(S).

% ---- 義務內容的涵攝：極性是資料，不是每種方向各寫一組規則 ----
% complies(方向, 值)：義務內容取這個值時合規。禁止是義務的對極。
% 許可兩個值都合規（either）：不做所許可之事不是違反，未知也不必補事實。
flip(t,f). flip(f,t).
complies(obligation, t).
complies(prohibition, f).
complies(permission, t).
complies(permission, f).
either(D) :- complies(D,t), complies(D,f).
outcome(S,violation) :- status(S,applies), deontic(S,D), not either(D),
                        complies(D,V), flip(V,W), pv(S,duty,W).
outcome(S,compliant) :- status(S,applies), deontic(S,D), not either(D),
                        complies(D,V), pv(S,duty,V).
outcome(S,compliant) :- status(S,applies), deontic(S,D), either(D).
outcome(S,conditional_violation) :- status(S,conditional), deontic(S,D), not either(D),
                        complies(D,V), flip(V,W), pv(S,duty,W).
outcome(S,conditional_compliant) :- status(S,conditional), deontic(S,D), not either(D),
                        complies(D,V), pv(S,duty,V).
outcome(S,conditional_compliant) :- status(S,conditional), deontic(S,D), either(D).
outcome(S,undetermined) :- status(S,applies), deontic(S,D), not either(D), complies(D,_), pv(S,duty,u).
outcome(S,undetermined) :- status(S,conditional), deontic(S,D), not either(D), complies(D,_), pv(S,duty,u).
outcome(S,undetermined) :- status(S,undetermined).
outcome(S,excluded)       :- status(S,excluded).
outcome(S,not_applicable) :- status(S,not_applicable).


% 候選規範：案件事實觸及該陳述（或其但書）的任一概念者才輸出，否則整份時點內的陳述都會被列為「無法判定」。
relevant(S) :- cond(_,S,_,_,M,_,_), given(M).
relevant(S) :- exception(E,S), cond(_,E,_,_,M,_,_), given(M).
verdict(S,V) :- outcome(S,V), relevant(S).

% ---- 主體的責任基礎：罰則的主體槽位標有責任基礎，行為人所屬的基礎要在其中 ----
% 主體槽位的每個擇一的值都標了基礎，才限制主體；有一個沒標，該值不限基礎，主體不受限。
% 呼叫端沒說明行為人屬哪種基礎則未知（缺事實），說明了而不在其中則不成立。
constrained(P) :- subject_basis(P,_), not subject_any(P).
actor_stated   :- actor_basis(_).
subj(P,t) :- sanction(P,_,_,_,_), not constrained(P).
subj(P,t) :- constrained(P), subject_basis(P,B), actor_basis(B).
subj(P,f) :- constrained(P), actor_stated, not subj(P,t).
subj(P,u) :- constrained(P), not actor_stated.

% ---- 連到罰則：只列候選，額度原文由 PG 提供 ----
% 罰則自己的要件（情節重大、必要時、屆期未改善）也要涵攝：被處罰的陳述違反時，罰則的適用範圍
% 成立則為候選，未知則附條件，不成立則不列；被處罰的陳述只是附條件違反時，罰則成立或未知都附條件。
% 以另一個效果為前提的罰則（觸發），前提的罰則確定列出才確定，只附條件列出則附條件，不列則不列；
% 數個前提彼此是「或」。前提屆期未履行與否、處分的金額門檻，是被觸發者自己的要件。
live(t). live(u).
viol(S,t) :- outcome(S,violation).
viol(S,u) :- outcome(S,conditional_violation).
arm(P,t) :- sanction(P,_,_,_,_), not triggered(P).
arm(P,t) :- trigger(P,Q,_), listed(Q,_,_,_,_).
arm(P,u) :- trigger(P,Q,_), listed_cond(Q,_,_,_,_).
candidate_penalty(P,S,Eff,Min,Max)      :- sanction(P,S,Eff,Min,Max), viol(S,t), pv(P,scope,t), arm(P,t),
                                           subj(P,t).
candidate_penalty_cond(P,S,Eff,Min,Max) :- sanction(P,S,Eff,Min,Max), viol(S,X), pv(P,scope,Y), arm(P,Z),
                                           subj(P,W), live(X), live(Y), live(Z), live(W),
                                           not candidate_penalty(P,S,Eff,Min,Max).
% 主體不成立而不列出的罰則：其餘要件若成立或未知，原本會是候選。
subject_mismatch(P) :- sanction(P,S,_,_,_), viol(S,X), pv(P,scope,Y), arm(P,Z),
                       live(X), live(Y), live(Z), subj(P,f).
pen_live(P) :- sanction(P,S,_,_,_), outcome(S,violation), arm(P,_).
pen_live(P) :- sanction(P,S,_,_,_), outcome(S,conditional_violation), arm(P,_).

% ---- 衝突與優先：只在有明文優先關係時消解，否則列為未決 ----
holds(S) :- status(S,applies).
opposite(prohibition,permission). opposite(permission,prohibition).
opposite(obligation,prohibition). opposite(prohibition,obligation).
conflict(S1,S2) :- holds(S1), holds(S2), S1 < S2, action(S1,A), action(S2,A),
                   deontic(S1,D1), deontic(S2,D2), opposite(D1,D2).
resolved(S1,S2) :- conflict(S1,S2), override(S1,S2,_).
resolved(S1,S2) :- conflict(S1,S2), override(S2,S1,_).
defeated(L, W, B) :- conflict(S1,S2), override(S1,S2,B), W = S1, L = S2.
defeated(L, W, B) :- conflict(S1,S2), override(S2,S1,B), W = S2, L = S1.
unresolved_conflict(S1,S2) :- conflict(S1,S2), not resolved(S1,S2).

% ---- 缺少的事實與問題 ----
unk_concept(S,M,C) :- cond(C,S,R,K,M,_,_), unk(C), not clause_f(S,R,K).
% 缺的是「事實」：只在結論因它而未定時列出；授權解析失敗另列 problem，不冒充缺事實。
missing(S,M) :- verdict(S,undetermined), unk_concept(S,M,C), not judgement(C), not given(M).
needs_judgement(S,M) :- verdict(S,undetermined), unk_concept(S,M,C), judgement(C), not given(M).
missing(S,M) :- verdict(S,undetermined), exception(E,S), unk_concept(E,M,C), not judgement(C), not given(M).
needs_judgement(S,M) :- verdict(S,undetermined), exception(E,S), unk_concept(E,M,C), judgement(C), not given(M).
missing(S,M) :- verdict(S,conditional_violation), exception(E,S), unk_concept(E,M,C), not judgement(C), not given(M).
needs_judgement(S,M) :- verdict(S,conditional_violation), exception(E,S), unk_concept(E,M,C), judgement(C), not given(M).
missing(P,M)          :- pen_live(P), pv(P,scope,u), unk_concept(P,M,C), not judgement(C), not given(M).
needs_judgement(P,M)  :- pen_live(P), pv(P,scope,u), unk_concept(P,M,C), judgement(C), not given(M).
missing(P,"liability_basis") :- pen_live(P), pv(P,scope,Y), live(Y), subj(P,u).
problem(unresolved_delegation, C) :- delegated(C), not thr(C,_).
% 許可量值沒提供：缺的是（許可，概念）這筆事實，不是概念本身，另列。
unk_permit(S,K,M) :- cond(C,S,R,Kl,M,_,_), permit_cond(C,K), unk(C), not clause_f(S,R,Kl),
                     not permit_given(K,M).
missing_permit(S,K,M) :- verdict(S,undetermined), unk_permit(S,K,M).
missing_permit(P,K,M) :- pen_live(P), pv(P,scope,u), unk_permit(P,K,M).
% 認定方式：事實給了而方式不符或沒給，要件未知，列為問題；限值沒有標而照常比較，也列出來。
problem(measurement_mismatch, C) :- meas_bad(C), cond(C,_,_,_,M,_,_), given(M).
problem(measurement_unlabeled, C) :- lim_unlabeled(C), v(C,_).

% ---- 依據軌跡：每個原子的取值、所用門檻值與其來源 ----
trace(C,S,M,V,T,R) :- cond(C,S,R,_,M,_,_), fact(M,V), thr(C,T), v(C,_).
trace_src(C,L)     :- thr_src(C,L).
% 結論用到了呼叫端對需判斷要件的判斷：依據軌跡要標出來，審核者才知道結論倚賴一個判斷。
judgement_used(S,M) :- cond(C,S,_,_,M,_,_), judgement(C), fact(M,_), v(C,_).
judgement_used(S,M) :- cond(C,S,_,_,M,_,_), judgement(C), cfact(M,_), v(C,_).
judgement_used(S,M) :- exception(E,S), cond(C,E,_,_,M,_,_), judgement(C), fact(M,_), v(C,_).
judgement_used(S,M) :- exception(E,S), cond(C,E,_,_,M,_,_), judgement(C), cfact(M,_), v(C,_).

% ---- 不是行政罰的效果：改善命令等預防性處分（order）與不法利得的追繳（disgorgement） ----
% 兩者都不以故意過失為要件，行政罰法總則的排除、減輕、時效與一行為不適用。
non_penal(order). non_penal(disgorgement).
penal(E) :- sanction(_,_,E,_,_), not non_penal(E).

% ---- 行政罰法總則：排除的是處罰，不是義務（03-04 §5.3） ----
% 案件事實用保留代碼。布林以正值為是、0 為否；年齡是歲。未提供不等於否定，
% 只有條文寫明「有事實才排除」或「未知則不套用」的，才依該句。
yes(C) :- fact(C,V), V > 0.
no(C)  :- fact(C,0).
culpable :- yes("ap_intent").
culpable :- yes("ap_negligence").
culpable :- yes("ap_agent_fault").
excuse(no_fault) :- no("ap_intent"), no("ap_negligence"), not yes("ap_agent_fault").
excuse(under_fourteen) :- fact("ap_age",V), V < 14000.
excuse(incapable) :- yes("ap_incapable"), no("ap_self_induced").
excuse(pursuant_to_law) :- yes("ap_pursuant_to_law").
excuse(superior_order) :- yes("ap_superior_order"), no("ap_knew_illegal").
excuse(superior_order) :- yes("ap_superior_order"), yes("ap_knew_illegal"), yes("ap_stated_opinion").
excuse(self_defense) :- yes("ap_self_defense"), not yes("ap_defense_excessive").
excuse(necessity) :- yes("ap_necessity"), not yes("ap_necessity_excessive").
withheld :- excuse(_).
% 有責性未知，或職務命令而已知與否未知：罰則改為附條件，不當成可罰。
soften :- not culpable, not excuse(no_fault).
soften :- yes("ap_superior_order"), not yes("ap_knew_illegal"), not no("ap_knew_illegal").
% 不是行政罰的效果不要有責性，不受總則的排除、減輕、時效與一行為約束。
listed(P,S,E,Min,Max)      :- candidate_penalty(P,S,E,Min,Max), non_penal(E).
listed_cond(P,S,E,Min,Max) :- candidate_penalty_cond(P,S,E,Min,Max), non_penal(E).
listed(P,S,E,Min,Max) :- candidate_penalty(P,S,E,Min,Max), penal(E), culpable, not withheld, not soften.
listed_cond(P,S,E,Min,Max) :- candidate_penalty(P,S,E,Min,Max), penal(E), not withheld, soften.
listed_cond(P,S,E,Min,Max) :- candidate_penalty_cond(P,S,E,Min,Max), penal(E), not withheld.
open(P) :- candidate_penalty(P,_,E,_,_), penal(E).
open(P) :- candidate_penalty_cond(P,_,E,_,_), penal(E).
barred(P,G) :- open(P), excuse(G).
mitigation(P,youth) :- open(P), fact("ap_age",V), V >= 14000, V < 18000, not withheld.
mitigation(P,reduced_capacity) :- open(P), yes("ap_capacity_reduced"), no("ap_self_induced"), not withheld.
mitigation(P,ignorance) :- open(P), yes("ap_ignorance_of_law"), not withheld.
mitigation(P,excessive_defense) :- open(P), yes("ap_self_defense"), yes("ap_defense_excessive"), not withheld.
mitigation(P,excessive_necessity) :- open(P), yes("ap_necessity"), yes("ap_necessity_excessive"), not withheld.
presumed(P,agent_fault) :- open(P), yes("ap_agent_fault"), not yes("ap_intent"), not yes("ap_negligence"), not withheld.

% 裁處權時效（行政罰法第 27 條）：只標記仍列出的罰則。三年的末日由日曆算出後傳入；
% 沒有 deadline 就是起算日不明，不標已逾。罰則留在候選裡。
time_barred(P) :- listed(P,_,E,_,_), penal(E), decision(D), deadline(T), D > T.
time_barred(P) :- listed_cond(P,_,E,_,_), penal(E), decision(D), deadline(T), D > T.

% ---- 罰則的競合：同一被處罰的陳述、同一效果，兩個仍列出的罰則才競合 ----
% 哪一個優先是解釋（特別規定的「同一事項」），只在有明文標註的優先關係時消解，否則列為未決。
live_pen(P,S,Eff) :- listed(P,S,Eff,_,_), penal(Eff).
live_pen(P,S,Eff) :- listed_cond(P,S,Eff,_,_), penal(Eff).
% 按次處罰是同一個罰鍰之後的反覆裁處，不與第一次的罰鍰互為替代：只有同為首次、或同為按次者才競合。
pkind(P,again) :- repeating(P).
pkind(P,once)  :- sanction(P,_,_,_,_), not repeating(P).
penalty_overlap(P1,P2) :- live_pen(P1,S,Eff), live_pen(P2,S,Eff), P1 < P2, pkind(P1,K), pkind(P2,K).
penalty_resolved(P1,P2) :- penalty_overlap(P1,P2), override(P1,P2,_).
penalty_resolved(P1,P2) :- penalty_overlap(P1,P2), override(P2,P1,_).
penalty_defeated(L,W,B) :- penalty_overlap(P1,P2), override(P1,P2,B), W = P1, L = P2.
penalty_defeated(L,W,B) :- penalty_overlap(P1,P2), override(P2,P1,B), W = P2, L = P1.
unresolved_penalty_overlap(P1,P2) :- penalty_overlap(P1,P2), not penalty_resolved(P1,P2).

% 一行為數法與數行為（行政罰法第 24、25 條）。數行為就是各則罰鍰自己的額度，不在這裡合併。
% 一行為只對確定列出、上下限都有、且不是按次的罰鍰算一個區間：上限是法定最高額中的最高者，
% 下限是各最低額中的最高者。附條件的不進來；有一則缺額度就不給合併區間。是否為一行為不選。
bounded_fine(P,Min,Max) :- listed(P,_,fine,Min,Max), Min != none, Max != none, not repeating(P).
open_fine :- listed(P,_,fine,Min,_), Min = none, not repeating(P).
open_fine :- listed(P,_,fine,_,Max), Max = none, not repeating(P).
some_fine :- bounded_fine(_,_,_).
one_floor(M) :- some_fine, M = #max{ Min,P : bounded_fine(P,Min,_) }.
one_cap(M) :- some_fine, M = #max{ Max,P : bounded_fine(P,_,Max) }.
one_act(Lo,Hi) :- one_floor(Lo), one_cap(Hi), not open_fine.

% ---- 輸出 ----
#show verdict/2.
#show candidate_penalty(P,S,Eff,Min,Max) : listed(P,S,Eff,Min,Max).
#show candidate_penalty_cond(P,S,Eff,Min,Max) : listed_cond(P,S,Eff,Min,Max).
#show subject_basis/2.
#show subject_mismatch/1.
#show barred/2.
#show mitigation/2.
#show presumed/2.
#show time_barred/1.
#show unresolved_conflict/2.
#show defeated/3.
#show missing/2.
#show needs_judgement/2.
#show missing_permit/3.
#show trigger/3.
#show judgement_used/2.
#show problem/2.
#show trace/6.
#show trace_src/2.
#show used_def/2.
#show penalty_defeated/3.
#show unresolved_penalty_overlap/2.
#show one_act/2.
