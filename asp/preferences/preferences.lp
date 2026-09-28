% preferences.lp — All preference rules for study plan navigation
%
% Contains shared helpers, external guard declarations, and all preference
% rules (soft constraints and hard constraints). Grounded once into the
% Control object; individual preferences toggled at runtime via
% assign_external() without rebuilding or re-grounding.
%
% Priority levels (runtime-configurable via pref_ordering_prio / pref_balance_prio externals):
%   standard_timeline — default @2, adjustable 1–10 via pref_ordering_prio(P)
%   credit_balancing  — default @1, adjustable 1–10 via pref_balance_prio(P)
%   @0 — reserved for diversity (handled externally, not in this file)


% ============================================================
% Shared helpers (unconditional — always computed)
% ============================================================

% Total credits assigned to semester I
sem_credits(I, S) :- I = 1..n, S = #sum{ V,E : in(E,s(I)), map(c,E,V) }.

% Number of modules assigned to semester I (used by LLM-generated custom preferences)
sem_count(I, C) :- I = 1..n, C = #count{ E : in(E,s(I)) }.


% ============================================================
% External guard declarations
% ============================================================

% --- standard_timeline (ordering) ---
#external pref_ordering.
#external pref_ordering_prio(P) : P = 1..10.

% Group assignments — lower group should come earlier in the plan.
%   Group 1 (foundations): BM, FM, AM
%   Group 2 (advanced):    PM
%   Group 3 (capstone):    IM
%   MSC is ungrouped — never penalized for ordering.
pgroup(1, b; 1, f; 1, a) :- pref_ordering.
pgroup(2, p)              :- pref_ordering.
pgroup(3, im)             :- pref_ordering.

% --- credit_balancing ---
#external pref_balance.
#external pref_balance_prio(P) : P = 1..10.


% ============================================================
% standard_timeline: ordering violation detection and penalty (default priority @2)
%
% A violation occurs when a foundational module M1 (low group number)
% is placed in the same semester or later than an advanced module M2
% (high group number). Group numbers are assigned via pgroup/2, above.
% ============================================================
in(im,im). % required to make im a set in and of itself
pref_violation((M1,s(I1),G1),(M2,s(I2),G2)) :-
    pref_ordering,
    in(M1,s(I1)), in(M2,s(I2)),
    M1 != M2,
    I1 >= I2,
    pgroup(G1,MAP1), pgroup(G2,MAP2),
    G1 < G2,
    in(M1,MAP1), in(M2,MAP2).

:~ pref_ordering, pref_violation(X,Y), pref_ordering_prio(P). [1@P, X, Y, P]


% ============================================================
% credit_balancing: minimize pairwise credit gap (default priority @1)
%
% For every pair of semesters (I, J) with I < J, penalize the absolute
% credit difference D. Pushes the solver toward equal workload.
% ============================================================

:~ pref_balance, sem_credits(I, S1), sem_credits(J, S2),
   I < J, D = |S1 - S2|, D > 0, pref_balance_prio(P). [D@P, I, J]
