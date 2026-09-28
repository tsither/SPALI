% #const n = 4.

% extra((bm1;bm2;bm3),bm,"BM").
% extra((fm1;fm2;fm3),fm,"FM").
% extra((am11;am12;am21),am,"AM").
% extra((am22;am31;am32),am,"AM").
% extra((pm1;pm2;pm3),pm,"PM").
% extra(im,im,"IM").
% extra(msc,msc,"MSC").

% b, f, a, o and p               % c
in((bm1;bm2;bm3),b).             map(c,(bm1;bm2;bm3),9).    % basic modules belong to set b and contain 9 credits each.
in((fm1;fm2;fm3),f).             map(c,(fm1;fm2;fm3),6).    % FM modules belong to set f and contain 6 credits each.
in((am11;am12;am21),a).          map(c,(am11;am12;am21),6). % AM modules belong to set a and contain 6 credits each.
in((am22;am31;am32),a).          map(c,(am22;am31;am32),6).
in(E,o) :- in(E,(f;a)).          map(c,(pm1;pm2;pm3),12).   % FM and AM modules in set f and a are OPTIONAL and are also in set o. PM modules contain 12 credits each.
in((pm1;pm2;pm3),p).             map(c,im,15).          % PM modules belong to set p, IM module contains 15 credits.
                                 map(c,msc,30).         % msc module contains 30 credits.

% m                             % s                                 % m: pool of all possible modules | s: study plan, subset of m
in(E,m) :- in(E,(b;f;a;p)).      map(s,bm1,w;s,bm2,s;s,bm3,w). % map basic modules to associated semesters
in((im;msc),m).                  map(s,(fm1;fm2;fm3),w).    % fm1, fm2 and fm3 are offered only in winter
                                 map(s,(am11;am12;am21),e). % ams are offered every semester.
                                 map(s,(am22;am31;am32),e).
                                 map(s,(pm1;pm2;pm3),e).
                                 map(s,(im;msc),e).         % im and msc are offered every semester.

%fm1 is exogenous and imposed by examination board, meaning it is mandatory in this instance
                                 % l and u
                                 map(l,b,27;l,o,24;l,p,24;l,m,120). %lower bound credit points for each module type
                                 map(u,b,27;u,o,24;u,p,24;u,m,120). %upper bound credit points for each module type

% global constraints
card(e,leq,2).  equal(int(s,f),e). % Exogenous modules: mandates that the number of exogenous modules must be less than or equal to 2 & students can only take their exogenous FMs
sum(int(H,s),c,bw,(L,U)) :- H = (b;o;p;m), map(l,H,L), map(u,H,U).  % enforces that credits points per module type are between lower and upper bounds.
in(im,gc3). in(msc,gc3). subseteq(gc3,s).   % the im and msc are subsets of study plan s, meaning they are mandatory modules.

% temporal constraints
empty(int(s(I),s(J)))       :- I = 1..n, J = 1..n, I < J.       % The same module can't be taken more than once.
empty(int(m(w),s(2*K  )))   :- K = 1..n, 1 <= 2*K,   2*K   <= n. % modules designated for winter semesters can't be taken in summer semesters.
empty(int(m(s),s(2*K-1)))   :- K = 1..n, 1 <= 2*K-1, 2*K-1 <= n. % modules designated for summer semesters can't be taken in winter semesters.
in(msc,tc4). sum(before(tc4),c,geq,90).         % Students must accumulate at least 90 credits before starting the msc module.



% in/2. denotes membership in a set, e.g., in(E,s(I)) means that the element E is in the set s(I).
% map/3. denotes a mapping, e.g., map(c,(bm1;bm2;bm3),9) means that the set c contains the element 9 for the mapping bm1, bm2, and bm3.
% empty/1. denotes an empty set, e.g., empty(int(s(I),s(J))) means that the set int(s(I),s(J)) is empty.
% subseteq/2. denotes a subset relation, e.g., subseteq(A,B) means that A is a subset of B.
% equal/2. denotes equality of sets, e.g., equal(A,B) means that A and B are equal.
% card/3. denotes the cardinality of a set, e.g., card(A,leq,U) means that the cardinality of A is less than or equal to U.
% sum/4. denotes a sum over a set, e.g., sum(A,F,bw,(L,U)) means that the sum of F over A is between L and U.
% tc4: temporal constraint - msc can only be taken after accumulating 90 credits 'sum of credits before set tc4 >= 90, msc in set tc4'
% gc3: global constraint - im and msc are mandatory modules 'im and msc in set gc3, gc3 subset of s'

#show .


% in((bm1;bm2;bm3),b).             map(c,(bm1;bm2;bm3),9).
% in((fm1;fm2;fm3),f).             map(c,(fm1;fm2;fm3),6).
% in((am11;am12;am21),a).          map(c,(am11;am12;am21),6).
% in((am22;am31;am32),a).          map(c,(am22;am31;am32),6).
% in(E,o) :- in(E,(f;a)).          map(c,(pm1;pm2;pm3),12).
% in((pm1;pm2;pm3),p).             map(c,im,15).
%                                  map(c,msc,30).

% in(E,m) :- in(E,(b;f;a;p)).      map(s,bm1,w;s,bm2,s;s,bm3,w).
% in((im;msc),m).                  map(s,(fm1;fm2;fm3),w).
%                                  map(s,(am11;am12;am21),e).
%                                  map(s,(am22;am31;am32),e).
%                                  map(s,(pm1;pm2;pm3),e).
%                                  map(s,(im;msc),e).

%                                  map(l,b,27;l,o,24;l,p,24;l,m,120).
%                                  map(u,b,27;u,o,24;u,p,24;u,m,120).

% % global constraints
% card(e,leq,2).  equal(int(s,f),e).
% sum(int(H,s),c,bw,(L,U)) :- H = (b;o;p;m), map(l,H,L), map(u,H,U).
% in(im,gc3). in(msc,gc3). subseteq(gc3,s).

% % temporal constraints
% empty(int(s(I),s(J)))       :- I = 1..n, J = 1..n, I < J.
% empty(int(m(w),s(2*K  )))   :- K = 1..n, 1 <= 2*K,   2*K   <= n.
% empty(int(m(s),s(2*K-1)))   :- K = 1..n, 1 <= 2*K-1, 2*K-1 <= n.
% in(msc,tc4). sum(before(tc4),c,geq,90).
