
requested_course(T) :- requested(T,M).

#external allow_partial_assignment. % True during opt-mode batch enumeration

% Opt mode: solver picks at most one module per course from the candidates
{ chosen(T,M) : requested(T,M) } 1 :- requested_course(T), allow_partial_assignment.

% Brave/committed mode: the single resolved module is assigned directly, no choice
chosen(T,M) :- requested(T,M), not allow_partial_assignment.

:- chosen(T1,M), chosen(T2,M), T1 != T2. % Two titles can't share module
:- chosen(T,M), current_semester(S), not in(M,s(S)). % Chosen module must be in current semester

% Avoid a single course assigned to multiple modules
:- chosen(T,M1), chosen(T,M2), M1 != M2.


#external committed(T,M) : course(T,M).
chosen(T,M) :- committed(T,M).

:~ allow_partial_assignment, requested_course(T), not chosen(T,_). [1@1,T]


% track already assigned modules to determine availability
assigned(M,S) :- completed_modules(M,S).
assigned(M,S) :- chosen(T,M), current_semester(S).

:- assigned(M,S), not in(M,s(S)).

% Can't select non-assigned bridge modules
not_available(M) :- in(M,f), not bridge_modules(M).
% Can't select already assigned modules
not_available(M) :- assigned(M,_).

% Allow selection of non-yet completed bridge modules
available(M,S) :- not assigned(M,_), in(M,e), in(M,s(S)), current_semester(S).

% If a module is not yet assigned, and it is not not_available, and it exists validly in this semester, it is available
available(M,S) :- not assigned(M,_), in(M,s(S)), not not_available(M), current_semester(S).

% New predicate for simplification
available_course(T,M) :- course(T,M), available(M,S), not requested(T,_).
#show available_course/2.
% #show available/2. #show requested/2.

%  #project chosen/2 is added in python

% Enforce max number of semesters preference, modules can't be assigned beyond that
max(X):- X = #max{S : in(_,s(S))}.
:- max(X), n_semesters_preference(Y), X!=Y.
:- completed_modules(M,N), not in(M,s(N)).


% Lock the past and the current semester to exactly the modules the student
% declared: completed modules for S < current, chosen courses for S = current.
% Without this, the generate rule leaves past semesters as free capacity that
% the optimizer happily backfills — e.g. moving a mandatory bridge module into
% semester 1 to balance credits, which the student can neither explain nor undo.
% Activated once the navigation phase starts (Phase 4).
#external lock_history.
:- lock_history, in(M,s(S)), current_semester(C), S <= C, not assigned(M,S).
