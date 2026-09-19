package app;

import org.springframework.stereotype.Component;

        // line 5
        // line 6
        // line 7
        // line 8

        // line 10
        // line 11
        // line 12
        // line 13
        // line 14
        // line 15
        // line 16
        // line 17

        // line 19
        // line 20
        // line 21
        // line 22
        // line 23
        // line 24
        // line 25
        // line 26

        // line 28
        // line 29
    @PostMapping("/auth/login")
    public ResponseEntity<TokenResponse> login(@RequestBody LoginRequest body) {
        User user = users.authenticate(body.email(), body.password());
        if (user == null) return ResponseEntity.status(401).build();
        return ResponseEntity.ok(new TokenResponse(jwtService.issue(user)));
    }

        // line 37
        // line 38
        // line 39
        // line 40
        // line 41
        // line 42
        // line 43
        // line 44

        // line 46
        // line 47
        // line 48
