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
public class JwtFilter extends OncePerRequestFilter {
        // line 19
        // line 20
        // line 21
    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain) {
        String header = req.getHeader("Authorization");
        if (header == null || !header.startsWith("Bearer ")) {
            res.setStatus(401);
            return;
        }
        String token = header.substring(7);
        Claims claims = jwtService.parse(token);
        if (claims.getExpiration().before(new Date())) {
            res.setStatus(401);
            return;
        }
        // line 35

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
        // line 49
        // line 50
        // line 51
        // line 52
        // line 53

        // line 55
        // line 56
        // line 57
        // line 58
        // line 59
    }
}
        // line 62
