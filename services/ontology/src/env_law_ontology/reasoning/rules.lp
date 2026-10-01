% 通用推論程式：法條不寫成程式，陳述是資料（事實輸入）。數值一律為千分位整數。
% 設計見 env-law-docs 01-02 §4、02-05 §5；事實由 facts.py 自資料庫產生，案件事實由呼叫端提供。
% 輸入：statement/3 cond/7 thr/2 thr_src/2 delegated/1 exception/2 override/3 sanction/5 action/2 src/2（由 PG 依 as-of 日期產生）
%       judgement/1（需判斷的要件原子）  unsupported/1（本程式不會比較的要件原子）  repeating/1（按次處罰的罰則）
%       fact_in/2（案件事實，由呼叫端提供；同一概念可帶多個值，矛盾由下方檢出）
role(scope;duty).

% ---- 矛盾事實：同一概念有兩個不同的值，該概念視同沒有可用的值，並列為問題 ----
% 不擇一、不取平均：哪一個值對，是事實認定，不是推論。矛盾的概念不冒充「缺事實」，
% 另列 problem；只有因它而未定的結論受影響，與它無關的結論照常輸出。
conflicting(M) :- fact_in(M,V1), fact_in(M,V2), V1 < V2.
fact(M,V)      :- fact_in(M,V), not conflicting(M).
problem(contradictory_fact, M) :- conflicting(M).
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

% ---- 義務內容的涵攝：違反、合規、附條件違反、無法判定 ----
outcome(S,violation)             :- status(S,applies),     deontic(S,obligation), pv(S,duty,f).
outcome(S,compliant)             :- status(S,applies),     deontic(S,obligation), pv(S,duty,t).
outcome(S,conditional_violation) :- status(S,conditional), deontic(S,obligation), pv(S,duty,f).
outcome(S,conditional_compliant) :- status(S,conditional), deontic(S,obligation), pv(S,duty,t).
outcome(S,undetermined) :- status(S,applies),     deontic(S,obligation), pv(S,duty,u).
outcome(S,undetermined) :- status(S,conditional), deontic(S,obligation), pv(S,duty,u).
outcome(S,undetermined) :- status(S,undetermined).
outcome(S,excluded)       :- status(S,excluded).
outcome(S,not_applicable) :- status(S,not_applicable).


% 候選規範：案件事實觸及該陳述（或其但書）的任一概念者才輸出，否則整份時點內的陳述都會被列為「無法判定」。
relevant(S) :- cond(_,S,_,_,M,_,_), fact_in(M,_).
relevant(S) :- exception(E,S), cond(_,E,_,_,M,_,_), fact_in(M,_).
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
missing(S,M) :- verdict(S,undetermined), unk_concept(S,M,C), not judgement(C), not fact_in(M,_).
needs_judgement(S,M) :- verdict(S,undetermined), unk_concept(S,M,C), judgement(C), not fact_in(M,_).
missing(S,M) :- verdict(S,undetermined), exception(E,S), unk_concept(E,M,C), not judgement(C), not fact_in(M,_).
needs_judgement(S,M) :- verdict(S,undetermined), exception(E,S), unk_concept(E,M,C), judgement(C), not fact_in(M,_).
missing(S,M) :- verdict(S,conditional_violation), exception(E,S), unk_concept(E,M,C), not judgement(C), not fact_in(M,_).
needs_judgement(S,M) :- verdict(S,conditional_violation), exception(E,S), unk_concept(E,M,C), judgement(C), not fact_in(M,_).
missing(P,M)          :- pen_live(P), pv(P,scope,u), unk_concept(P,M,C), not judgement(C), not fact_in(M,_).
needs_judgement(P,M)  :- pen_live(P), pv(P,scope,u), unk_concept(P,M,C), judgement(C), not fact_in(M,_).
problem(unresolved_delegation, C) :- delegated(C), not thr(C,_).

% ---- 依據軌跡：每個原子的取值、所用門檻值與其來源 ----
trace(C,S,M,V,T,R) :- cond(C,S,R,_,M,_,_), fact(M,V), thr(C,T), v(C,_).
trace_src(C,L)     :- thr_src(C,L).
% 結論用到了呼叫端對需判斷要件的判斷：依據軌跡要標出來，審核者才知道結論倚賴一個判斷。
judgement_used(S,M) :- cond(C,S,_,_,M,_,_), judgement(C), fact(M,_), v(C,_).
judgement_used(S,M) :- exception(E,S), cond(C,E,_,_,M,_,_), judgement(C), fact(M,_), v(C,_).

% ---- 罰則的競合：同一被處罰的陳述、同一效果，兩個罰則同時成為候選 ----
% 哪一個優先是解釋（特別規定的「同一事項」），只在有明文標註的優先關係時消解，否則列為未決。
live_pen(P,S,Eff) :- candidate_penalty(P,S,Eff,_,_).
live_pen(P,S,Eff) :- candidate_penalty_cond(P,S,Eff,_,_).
% 按次處罰是同一個罰鍰之後的反覆裁處，不與第一次的罰鍰互為替代：只有同為首次、或同為按次者才競合。
pkind(P,again) :- repeating(P).
pkind(P,once)  :- sanction(P,_,_,_,_), not repeating(P).
penalty_overlap(P1,P2) :- live_pen(P1,S,Eff), live_pen(P2,S,Eff), P1 < P2, pkind(P1,K), pkind(P2,K).
penalty_resolved(P1,P2) :- penalty_overlap(P1,P2), override(P1,P2,_).
penalty_resolved(P1,P2) :- penalty_overlap(P1,P2), override(P2,P1,_).
penalty_defeated(L,W,B) :- penalty_overlap(P1,P2), override(P1,P2,B), W = P1, L = P2.
penalty_defeated(L,W,B) :- penalty_overlap(P1,P2), override(P2,P1,B), W = P2, L = P1.
unresolved_penalty_overlap(P1,P2) :- penalty_overlap(P1,P2), not penalty_resolved(P1,P2).

% 概念值的比較（行業屬某行業）本程式不做：誠實列為問題，不讓結論悄悄停在未知。
problem(unsupported_condition, C) :- unsupported(C).

% ---- 輸出 ----
#show verdict/2.
#show candidate_penalty/5.
#show candidate_penalty_cond/5.
#show unresolved_conflict/2.
#show defeated/3.
#show missing/2.
#show needs_judgement/2.
#show judgement_used/2.
#show problem/2.
#show trace/6.
#show trace_src/2.
#show penalty_defeated/3.
#show unresolved_penalty_overlap/2.
