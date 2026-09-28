% generate
{ in(E,s(I)) } :- in(E,m), I=1..n. % generate all possible study plans

% s = s(1) U ... U s(n)
in(E,s) :- in(E,s(I)).  % Union of all semesters to create a study plan set s

% m(w) and m(s)
in(E,m(X)) :- X = (s;w), in(E,m), map(s,E,X). % maps modules to semesters

% additional sets (Recognize different kinds of sets)
set(A) :-      empty(A).
set(A) :- subseteq(A,B).    set(A) :-   equal(A,B).
set(B) :- subseteq(A,B).    set(B) :-   equal(A,B).
set(A) :-   card(A,R,L).    set(A) :- sum(A,M,R,L).
%
set(A) :- set( int(A,B)).   set(B) :- set(int(A,B)). % if int(A,B) is a set, then A and B are also sets
set(A) :- set(before(A)).

%
in( E, int(A,B)) :- set( int(A,B)), in(E,A), in(E,B).       % if the intersection of two sets is a set
in(E1,before(A)) :- set(before(A)), in(E1,s(I)), in(E2,A), in(E2,s(J)), I < J. % define membership of the before operator

% constraints (eliminate invalid study plans)
:- empty(A), in(E,A). % Something can't be in an empty set - defining 'empty'
%
:- subseteq(A,B), in(E,A), not in(E,B). % define 'subseteq'
%
:- equal(A,B), in(E,A), not in(E,B).    % define 'equal'
:- equal(A,B), not in(E,A), in(E,B).    % define 'equal'
%
:- card(A,leq,U), not { in(E,A) } U.    % define 'cardinality'
%
:- sum(A,F,bw,(L,U)), not L #sum{ V,E : in(E,A), map(F,E,V) } U.    % define 'sum'
:- sum(A,F,geq,   L), not L #sum{ V,E : in(E,A), map(F,E,V) }.      % define 'sum'

% #show sum/4.
% #show .
% Placement display for standalone debugging; guarded so the atoms don't
% appear in app solves. Shows the named atom (crash-safe for the parsers).
%
% show_in_placements.
#external show_in_placements.
#show in(M, s(I)) : in(M, s(I)), show_in_placements.

% % generate
% { in(E,s(I)) } :- in(E,m), I=1..n. % generate all possible study plans

% % s = s(1) U ... U s(n)
% in(E,s) :- in(E,s(I)).  % Union of all semesters to create a study plan set s

% % m(w) and m(s)
% in(E,m(X)) :- X = (s;w), in(E,m), map(s,E,X). % maps modules to semesters

% set(A) :-      empty(A).
% set(A) :- subseteq(A,B).    set(A) :-   equal(A,B).
% set(B) :- subseteq(A,B).    set(B) :-   equal(A,B).
% set(A) :-   card(A,R,L).    set(A) :- sum(A,M,R,L).
% %
% set(A) :- set( int(A,B)).   set(B) :- set(int(A,B)).
% set(A) :- set(before(A)).

% in( E, int(A,B)) :- set( int(A,B)), in(E,A), in(E,B).
% in(E1,before(A)) :- set(before(A)), in(E1,s(I)), in(E2,A), in(E2,s(J)), I < J.

% % integrity constraints
% :- empty(A), in(E,A).
% %
% :- subseteq(A,B), in(E,A), not in(E,B).
% %
% :- equal(A,B), in(E,A), not in(E,B).
% :- equal(A,B), not in(E,A), in(E,B).
% %
% :- card(A,leq,U), not { in(E,A) } U.

% :- sum(A,F,bw,(L,U)), not L #sum{ V,E : in(E,A), map(F,E,V) } U.
% :- sum(A,F,geq,   L), not L #sum{ V,E : in(E,A), map(F,E,V) }.
