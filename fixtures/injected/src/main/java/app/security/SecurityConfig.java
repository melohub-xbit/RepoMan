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
        // line 30
        // line 31
        // line 32
        // line 33
        // line 34
        // line 35

        // line 37
        // line 38
        // line 39
        // line 40
    @Bean
    SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .csrf(csrf -> csrf.disable())
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/auth/**").permitAll()
                .requestMatchers("/admin/**").hasRole("ADMIN")
                .requestMatchers("/api/**").authenticated()
                .anyRequest().permitAll())
            .addFilterBefore(jwtFilter, UsernamePasswordAuthenticationFilter.class);
        return http.build();
    }
        // line 53

        // line 55
        // line 56
        // line 57
        // line 58
        // line 59
        // line 60
        // line 61
        // line 62

        // line 64
        // line 65
        // line 66
        // line 67
        // line 68
        // line 69
        // line 70
