FROM nineseconds/mtg:2 AS mtg

FROM alpine:3.20
COPY --from=mtg /mtg /mtg
COPY start.sh /start.sh
RUN sed -i 's/\r$//' /start.sh && chmod +x /start.sh
ENV PORT=8443
EXPOSE 8443
ENTRYPOINT ["/start.sh"]
