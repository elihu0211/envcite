% 通用推論程式：法條不寫成程式，陳述是資料（事實輸入）。數值一律為千分位整數。
% 事實由 facts.py 自資料庫產生，案件事實由呼叫端提供。
% 輸入：statement/3 cond/7 thr/2 thr_src/2 delegated/1 exception/2 override/3 sanction/5 action/2 src/2（由 PG 依 as-of 日期產生）
%       vcond/2（與概念值比較的要件原子）  stmt_law/2（陳述所屬法規）  defmember/3（法規內用語定義涵蓋的成員）
%       defsrc/4（同上，另帶定義編號，供依據軌跡）
%       cfact_in/2（案件的概念值事實：概念代碼 → 值概念代碼）
%       judgement/1（需判斷的要件原子）  unsupported/1（本程式不會比較的要件原子）  repeating/1（按次處罰的罰則）
%       fact_in/2（案件事實，由呼叫端提供；同一概念可帶多個值，矛盾由下方檢出）
role(scope;duty).

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
isa(L,X,T) :- defmember(L,T,Y), isa(L,X,Y).

% 依據軌跡：某個值之所以屬於比較的用語，所經過的每一則定義（可能是遞移的）。
contrib(L,X,T,D) :- defsrc(L,T,Y,D), isa(L,X,Y).
contrib(L,X,T,D) :- defsrc(L,T,Y,_), isa(L,X,Y), contrib(L,X,Y,D).
used_def(C,D)    :- vcond(C,V), cond(C,S,_,_,M,_,_), cfact(M,X), stmt_law(S,L),
                    isa(L,X,V), contrib(L,X,V,D).

prescriptive(S) :- statement(S,prescriptive,_).
deontic(S,D)    :- statement(S,prescriptive,D).

% ---- 要件原子的三值：t 成立、f 不成立、無（未知） ----
atom(C) :- cond(C,_,_,_,_,_,_).
raw(C,t) :- cond(C,_,_,_,M,le,_), thr(C,T), fact(M,V), V <= T.
raw(C,f) :- cond(C,_,_,_,M,le,_), thr(C,T), fact(M,V), V >  T.
raw(C,t) :- cond(C,_,_,_,M,lt,_), thr(C,T), fact(M,V), V <  T.
raw(C,f) :- cond(C,_,_,_,M,lt,_), thr(C,T), fact(M,V), V >= T.
raw(C,t) :- cond(C,_,_,_,M,ge,_), thr(C,T), fact(M,V), V >= T.
raw(C,f) :- cond(C,_,_,_,M,ge,_), thr(C,T), fact(M,V), V <  T.
raw(C,t) :- cond(C,_,_,_,M,gt,_), thr(C,T), fact(M,V), V >  T.
raw(C,f) :- cond(C,_,_,_,M,gt,_), thr(C,T), fact(M,V), V <= T.
raw(C,t) :- cond(C,_,_,_,M,eq,_), thr(C,T), fact(M,V), V == T.
raw(C,f) :- cond(C,_,_,_,M,eq,_), thr(C,T), fact(M,V), V != T.
raw(C,t) :- cond(C,_,_,_,M,ne,_), thr(C,T), fact(M,V), V != T.
raw(C,f) :- cond(C,_,_,_,M,ne,_), thr(C,T), fact(M,V), V == T.
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

% ---- 連到罰則：只列候選，額度原文由 PG 提供 ----
% 罰則自己的要件（情節重大、必要時、屆期未改善）也要涵攝：被處罰的陳述違反時，罰則的適用範圍
% 成立則為候選，未知則附條件，不成立則不列；被處罰的陳述只是附條件違反時，罰則成立或未知都附條件。
candidate_penalty(P,S,Eff,Min,Max)        :- sanction(P,S,Eff,Min,Max), outcome(S,violation), pv(P,scope,t).
candidate_penalty_cond(P,S,Eff,Min,Max)   :- sanction(P,S,Eff,Min,Max), outcome(S,violation), pv(P,scope,u).
candidate_penalty_cond(P,S,Eff,Min,Max)   :- sanction(P,S,Eff,Min,Max), outcome(S,conditional_violation), pv(P,scope,t).
candidate_penalty_cond(P,S,Eff,Min,Max)   :- sanction(P,S,Eff,Min,Max), outcome(S,conditional_violation), pv(P,scope,u).
pen_live(P) :- sanction(P,S,_,_,_), outcome(S,violation).
pen_live(P) :- sanction(P,S,_,_,_), outcome(S,conditional_violation).

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
problem(unresolved_delegation, C) :- delegated(C), not thr(C,_).

% ---- 依據軌跡：每個原子的取值、所用門檻值與其來源 ----
trace(C,S,M,V,T,R) :- cond(C,S,R,_,M,_,_), fact(M,V), thr(C,T), v(C,_).
trace_src(C,L)     :- thr_src(C,L).
% 結論用到了呼叫端對需判斷要件的判斷：依據軌跡要標出來，審核者才知道結論倚賴一個判斷。
judgement_used(S,M) :- cond(C,S,_,_,M,_,_), judgement(C), fact(M,_), v(C,_).
judgement_used(S,M) :- cond(C,S,_,_,M,_,_), judgement(C), cfact(M,_), v(C,_).
judgement_used(S,M) :- exception(E,S), cond(C,E,_,_,M,_,_), judgement(C), fact(M,_), v(C,_).
judgement_used(S,M) :- exception(E,S), cond(C,E,_,_,M,_,_), judgement(C), cfact(M,_), v(C,_).

% ---- 行政罰法總則：排除的是處罰，不是義務（01-02 §4.7） ----
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
% order（改善命令等預防性處分）不是行政罰：不要有責性，不受總則的排除、減輕、時效與一行為約束。
listed(P,S,order,Min,Max)      :- candidate_penalty(P,S,order,Min,Max).
listed_cond(P,S,order,Min,Max) :- candidate_penalty_cond(P,S,order,Min,Max).
listed(P,S,E,Min,Max) :- candidate_penalty(P,S,E,Min,Max), E != order, culpable, not withheld, not soften.
listed_cond(P,S,E,Min,Max) :- candidate_penalty(P,S,E,Min,Max), E != order, not withheld, soften.
listed_cond(P,S,E,Min,Max) :- candidate_penalty_cond(P,S,E,Min,Max), E != order, not withheld.
open(P) :- candidate_penalty(P,_,E,_,_), E != order.
open(P) :- candidate_penalty_cond(P,_,E,_,_), E != order.
barred(P,G) :- open(P), excuse(G).
mitigation(P,youth) :- open(P), fact("ap_age",V), V >= 14000, V < 18000, not withheld.
mitigation(P,reduced_capacity) :- open(P), yes("ap_capacity_reduced"), no("ap_self_induced"), not withheld.
mitigation(P,ignorance) :- open(P), yes("ap_ignorance_of_law"), not withheld.
mitigation(P,excessive_defense) :- open(P), yes("ap_self_defense"), yes("ap_defense_excessive"), not withheld.
mitigation(P,excessive_necessity) :- open(P), yes("ap_necessity"), yes("ap_necessity_excessive"), not withheld.
presumed(P,agent_fault) :- open(P), yes("ap_agent_fault"), not yes("ap_intent"), not yes("ap_negligence"), not withheld.

% 裁處權時效（行政罰法第 27 條）：只標記仍列出的罰則。三年的末日由日曆算出後傳入；
% 沒有 deadline 就是起算日不明，不標已逾。罰則留在候選裡。
time_barred(P) :- listed(P,_,E,_,_), E != order, decision(D), deadline(T), D > T.
time_barred(P) :- listed_cond(P,_,E,_,_), E != order, decision(D), deadline(T), D > T.

% ---- 罰則的競合：同一被處罰的陳述、同一效果，兩個仍列出的罰則才競合 ----
% 哪一個優先是解釋（特別規定的「同一事項」），只在有明文標註的優先關係時消解，否則列為未決。
live_pen(P,S,Eff) :- listed(P,S,Eff,_,_), Eff != order.
live_pen(P,S,Eff) :- listed_cond(P,S,Eff,_,_), Eff != order.
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

% 概念值的比較（行業屬某行業）本程式不做：誠實列為問題，不讓結論悄悄停在未知。
problem(unsupported_condition, C) :- unsupported(C).

% ---- 輸出 ----
#show verdict/2.
#show candidate_penalty(P,S,Eff,Min,Max) : listed(P,S,Eff,Min,Max).
#show candidate_penalty_cond(P,S,Eff,Min,Max) : listed_cond(P,S,Eff,Min,Max).
#show barred/2.
#show mitigation/2.
#show presumed/2.
#show time_barred/1.
#show unresolved_conflict/2.
#show defeated/3.
#show missing/2.
#show needs_judgement/2.
#show judgement_used/2.
#show problem/2.
#show trace/6.
#show trace_src/2.
#show used_def/2.
#show penalty_defeated/3.
#show unresolved_penalty_overlap/2.
#show one_act/2.
