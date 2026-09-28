% Defines domains of external atoms for the study regulations program

#external bridge_modules(M) : in(M,_). % Atoms built from program instance
#external in(M,e) : in(M,_). % Atoms built from program instance
#external requested(T,M) : course(T,M). % Atoms built from database
#external completed_modules(M,S) : in(M,m), S = 1..10. % Assigned from Python session state
