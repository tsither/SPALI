% diversity_external.lp — pre-declared prev/3 externals and diversity rules.
%
% Grounded once into the main Control object at startup.
% Between solve rounds, Python assigns prev(J, M, S) externals to True
% for each module M placed in semester S in previous plan J.
% After all rounds, Python resets all prev externals to False.
%
% k_max = 10: supports up to 10 diverse plans per solve session.
%
% diversity_schedule guards the whole mechanism — set it True before a
% diverse solve and False afterwards. Diversity is measured by
% (module, semester) placement pairs: two plans are "different" if the same
% module ends up in a different semester.

#external prev(J, M, S) : J = 0..9, in(M, m), S = 1..n.
#external diversity_schedule.

% % Minimize shared (module, semester) assignments across plans.
% % Priority @0 — the lowest level, so diversity only breaks ties between plans
% % that are already optimal under the preferences in preferences.lp.
% :~ diversity_schedule, prev(J, M, S), in(M, s(S)). [1@0, J, M, S]


div_plan(J) :- prev(J, _, _).

% Overlap of the current plan with each previous plan J.
div_overlap(J, C) :- diversity_schedule, div_plan(J),
                     C = #count{ M, S : prev(J, M, S), in(M, s(S)) }.

% The largest overlap against any single previous plan.
div_max(X) :- div_overlap(_, _), X = #max{ C : div_overlap(J, C) }.

:~ diversity_schedule, div_max(X). [X@0]
