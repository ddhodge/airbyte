/*
 * Copyright (c) 2022 Airbyte, Inc., all rights reserved.
 */

package io.airbyte.server.auth;

import io.airbyte.commons.auth.AuthRole;
import io.micronaut.core.util.StringUtils;
import io.micronaut.http.HttpRequest;
import io.micronaut.security.authentication.AuthenticationFailureReason;
import io.micronaut.security.authentication.AuthenticationProvider;
import io.micronaut.security.authentication.AuthenticationRequest;
import io.micronaut.security.authentication.AuthenticationResponse;
import jakarta.inject.Singleton;
import java.util.Set;
import java.util.stream.Collectors;
import lombok.extern.slf4j.Slf4j;
import org.reactivestreams.Publisher;
import reactor.core.publisher.Flux;

/**
 * Basic {@link AuthenticationProvider} that ensures that the basic authentication has been
 * provided.
 */
@Singleton
@Slf4j
public class AirbyteAuthenticationProvider implements AuthenticationProvider {

  @Override
  public Publisher<AuthenticationResponse> authenticate(final HttpRequest<?> httpRequest, final AuthenticationRequest<?, ?> authenticationRequest) {
    log.info("Authenticating identity {}...", authenticationRequest.getIdentity());

    final String username = (String) authenticationRequest.getIdentity();
    final AuthenticationResponse authenticationResponse =
        StringUtils.isNotEmpty(username) ? AuthenticationResponse.success(username, getDefaultRoles())
            : AuthenticationResponse.failure(
                AuthenticationFailureReason.USER_NOT_FOUND);

    return Flux.create(emitter -> {
      emitter.next(authenticationResponse);
      emitter.complete();
    });
  }

  private Set<String> getDefaultRoles() {
    return AuthRole.buildAuthRolesSet(AuthRole.OWNER).stream()
        .map(r -> r.getLabel())
        .collect(Collectors.toSet());
  }

}
